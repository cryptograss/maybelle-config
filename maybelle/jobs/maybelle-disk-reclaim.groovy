pipelineJob('maybelle-disk-reclaim') {
    description('Reclaim disk space on maybelle by pruning old Jenkins build history, ' +
                'workspace output dirs, and dangling Docker layers. Trigger manually when ' +
                'you see ENOSPC in a build; also runs weekly on Sundays as prevention.')

    properties {
        disableConcurrentBuilds()
    }

    logRotator {
        numToKeep(20)
        daysToKeep(60)
    }

    triggers {
        cron('H 4 * * 0')  // Sunday early morning, jitter-hashed to avoid a synced spike
    }

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        timeout(time: 30, unit: 'MINUTES')
                    }

                    stages {
                        stage('Before: disk snapshot') {
                            steps {
                                sh 'df -h /var/jenkins_home || true'
                                sh 'du -sh /var/jenkins_home/jobs/*/builds 2>/dev/null | sort -h | tail -20 || true'
                            }
                        }

                        stage('Trim build history to logRotator limits') {
                            steps {
                                // logRotator on each job normally handles this, but if it was
                                // ever missing (as of the fix that shipped alongside this job),
                                // years of builds accumulated first. Enforce a hard ceiling of
                                // 50 per job on this sweep. Safe because Jenkins' own rotator
                                // will trim further to per-job limits on next build.
                                sh \'\'\'
                                    for d in /var/jenkins_home/jobs/*/builds; do
                                        [ -d "$d" ] || continue
                                        job=$(basename $(dirname "$d"))
                                        keep=50
                                        count=$(ls -1 "$d" | grep -E "^[0-9]+$" | wc -l)
                                        if [ "$count" -le "$keep" ]; then
                                            echo "$job: $count builds (under $keep, skipping)"
                                            continue
                                        fi
                                        to_remove=$(($count - $keep))
                                        echo "$job: $count builds, trimming $to_remove"
                                        ls -1 "$d" | grep -E "^[0-9]+$" | sort -n | head -n "$to_remove" | while read n; do
                                            rm -rf "$d/$n"
                                        done
                                    done
                                \'\'\'
                            }
                        }

                        stage('Clear workspace output dirs') {
                            steps {
                                // arthel builds write generated pages into workspace/*/output;
                                // each build recreates them, so old ones just squat on disk.
                                sh \'\'\'
                                    find /var/jenkins_home/workspace -maxdepth 3 -type d \\
                                        \\( -name output -o -name vendor \\) 2>/dev/null | while read dir; do
                                        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
                                        echo "removing $dir ($size)"
                                        rm -rf "$dir"
                                    done
                                \'\'\'
                            }
                        }

                        stage('Prune Docker (if daemon reachable)') {
                            steps {
                                // The Jenkins container has docker CLI + socket mount for
                                // arthel builds that use docker. system prune -af reclaims
                                // dangling images and stopped containers.
                                sh \'\'\'
                                    if docker version > /dev/null 2>&1; then
                                        docker system prune -af --volumes || true
                                    else
                                        echo "docker not reachable from this container, skipping"
                                    fi
                                \'\'\'
                            }
                        }

                        stage('After: disk snapshot') {
                            steps {
                                sh 'df -h /var/jenkins_home || true'
                            }
                        }
                    }

                    post {
                        success { echo "Disk reclaim completed. Check the before/after snapshots for delta." }
                        failure { echo "Disk reclaim failed. Manual intervention may be needed." }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
}
