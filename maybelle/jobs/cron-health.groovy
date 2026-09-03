// Consolidated freshness checks for host-side cronjobs running on maybelle.
// Replaces: arthel-rsync-status, pickipedia-rsync-status, backup-pickipedia,
// backup-memory-lane (and the would-be delivery-kid-audit-status).
//
// Each stage independently checks one cron's freshness signal — a log line
// or backup-file mtime — against its own staleness threshold. Stages use
// catchError so one stale cron doesn't mask the rest; the build is FAILURE
// if any stage failed.
pipelineJob('cron-health') {
    description('Host-cron freshness checks (rsync deploys, backups, delivery-kid audit). Runs every 5 minutes; each stage has its own staleness threshold and runs independently.')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                        buildDiscarder(logRotator(numToKeepStr: '50'))
                    }

                    stages {
                        stage('arthel rsync (NFS deploy)') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        DEPLOY_LOG="/var/log/nfs-deploy.log"
                                        STALE_MINUTES=10

                                        echo "=== arthel rsync (NFS deploy) ==="
                                        if [ ! -f "\\$DEPLOY_LOG" ]; then
                                            echo "FAIL: log file missing at \\$DEPLOY_LOG"
                                            exit 1
                                        fi

                                        LAST=\\$(grep "Deploy successful" "\\$DEPLOY_LOG" | tail -1)
                                        if [ -z "\\$LAST" ]; then
                                            echo "FAIL: no successful deploys recorded yet"
                                            tail -10 "\\$DEPLOY_LOG"
                                            exit 1
                                        fi

                                        LAST_TIME=\\$(echo "\\$LAST" | sed 's/: Deploy successful//')
                                        LAST_EPOCH=\\$(date -d "\\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                        AGE=\\$(( (\\$(date +%s) - LAST_EPOCH) / 60 ))

                                        echo "Last successful: \\$LAST_TIME (\\$AGE min ago)"
                                        if [ "\\$AGE" -gt "\\$STALE_MINUTES" ]; then
                                            echo "FAIL: stale (>\\$STALE_MINUTES min)"
                                            tail -15 "\\$DEPLOY_LOG"
                                            exit 1
                                        fi
                                        echo "OK"
                                    """
                                }
                            }
                        }

                        stage('pickipedia rsync (VPS deploy)') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        DEPLOY_LOG="/var/log/pickipedia-deploy.log"
                                        PAUSE_FILE="/var/jenkins_home/.pickipedia-deploy-paused"
                                        MARKER_FILE="/var/jenkins_home/pickipedia_stage/.deploy-ready"
                                        STALE_MINUTES=10

                                        echo "=== pickipedia rsync (VPS deploy) ==="

                                        if [ -f "\\$PAUSE_FILE" ]; then
                                            echo "PAUSED: \\$(cat \\$PAUSE_FILE)"
                                            echo "(treating as healthy — deploys are intentionally stopped)"
                                            exit 0
                                        fi

                                        if [ ! -f "\\$DEPLOY_LOG" ]; then
                                            echo "FAIL: log file missing at \\$DEPLOY_LOG"
                                            exit 1
                                        fi

                                        LAST=\\$(grep -i "deploy successful" "\\$DEPLOY_LOG" | tail -1)
                                        if [ -z "\\$LAST" ]; then
                                            echo "FAIL: no successful deploys recorded yet"
                                            tail -10 "\\$DEPLOY_LOG"
                                            exit 1
                                        fi

                                        LAST_TIME=\\$(echo "\\$LAST" | sed 's/: .*[Dd]eploy successful//')
                                        LAST_EPOCH=\\$(date -d "\\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                        AGE=\\$(( (\\$(date +%s) - LAST_EPOCH) / 60 ))

                                        echo "Last successful: \\$LAST_TIME (\\$AGE min ago)"
                                        if [ "\\$AGE" -gt "\\$STALE_MINUTES" ]; then
                                            echo "FAIL: stale (>\\$STALE_MINUTES min)"
                                            echo ""
                                            echo "--- Marker file ---"
                                            if [ -f "\\$MARKER_FILE" ]; then
                                                MARKER_AGE=\\$(( (\\$(date +%s) - \\$(stat -c %Y "\\$MARKER_FILE")) / 60 ))
                                                echo "Marker EXISTS (\\$MARKER_AGE min): \\$(cat \\$MARKER_FILE)"
                                            else
                                                echo "Marker absent — Jenkins build cron may not be writing it"
                                            fi
                                            echo ""
                                            echo "--- Recent errors ---"
                                            grep -iE "error|fail|denied|refused|timeout" "\\$DEPLOY_LOG" | tail -5 || echo "(no errors found)"
                                            echo ""
                                            echo "--- Tail of log ---"
                                            tail -15 "\\$DEPLOY_LOG"
                                            exit 1
                                        fi
                                        echo "OK"
                                    """
                                }
                            }
                        }

                        stage('pickipedia backup') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        BACKUP_LOG="/var/log/pickipedia-backup.log"
                                        BACKUP_DIR="/mnt/persist/pickipedia/backups"
                                        STALE_HOURS=25

                                        echo "=== pickipedia backup ==="
                                        if [ ! -f "\\$BACKUP_LOG" ]; then
                                            echo "WARN: log file missing — backup may not have run yet"
                                            exit 0
                                        fi

                                        LAST=\\$(grep "Backup successful" "\\$BACKUP_LOG" | tail -1)
                                        if [ -z "\\$LAST" ]; then
                                            echo "FAIL: no successful backups recorded yet"
                                            tail -10 "\\$BACKUP_LOG"
                                            exit 1
                                        fi

                                        LAST_TIME=\\$(echo "\\$LAST" | cut -c1-19)
                                        LAST_EPOCH=\\$(date -d "\\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                        AGE=\\$(( (\\$(date +%s) - LAST_EPOCH) / 3600 ))

                                        echo "Last successful: \\$LAST_TIME (\\$AGE hours ago)"
                                        if [ "\\$AGE" -gt "\\$STALE_HOURS" ]; then
                                            echo "FAIL: stale (>\\$STALE_HOURS hours)"
                                            tail -15 "\\$BACKUP_LOG"
                                            exit 1
                                        fi

                                        # Sanity: latest .sql.gz should not be tiny
                                        if [ -d "\\$BACKUP_DIR" ]; then
                                            LATEST=\\$(ls -t "\\$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -1)
                                            if [ -n "\\$LATEST" ]; then
                                                SIZE=\\$(stat -c%s "\\$LATEST")
                                                echo "Latest backup file: \\$(basename \\$LATEST) (\\$SIZE bytes)"
                                                if [ "\\$SIZE" -lt 1000 ]; then
                                                    echo "FAIL: backup file suspiciously small"
                                                    exit 1
                                                fi
                                            fi
                                        fi
                                        echo "OK"
                                    """
                                }
                            }
                        }

                        stage('memory-lane backup') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        BACKUP_DIR="/mnt/persist/magenta/backups"
                                        STALE_HOURS=3

                                        echo "=== memory-lane backup ==="
                                        if [ ! -d "\\$BACKUP_DIR" ]; then
                                            echo "FAIL: backup directory missing at \\$BACKUP_DIR"
                                            exit 1
                                        fi

                                        LATEST=\\$(ls -t "\\$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
                                        if [ -z "\\$LATEST" ]; then
                                            echo "FAIL: no .dump files found"
                                            exit 1
                                        fi

                                        AGE=\\$(( (\\$(date +%s) - \\$(stat -c%Y "\\$LATEST")) / 3600 ))
                                        SIZE=\\$(numfmt --to=iec \\$(stat -c%s "\\$LATEST"))
                                        echo "Latest dump: \\$(basename \\$LATEST) — \\$SIZE — \\$AGE hours old"

                                        if [ "\\$AGE" -gt "\\$STALE_HOURS" ]; then
                                            echo "FAIL: stale (>\\$STALE_HOURS hours)"
                                            ls -lht "\\$BACKUP_DIR"/*.dump 2>/dev/null | head -5
                                            exit 1
                                        fi
                                        echo "OK"
                                    """
                                }
                            }
                        }

                        stage('delivery-kid audit') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        AUDIT_LOG="/var/log/delivery-kid-audit.log"
                                        STALE_MINUTES=70

                                        echo "=== delivery-kid audit ==="
                                        if [ ! -f "\\$AUDIT_LOG" ]; then
                                            echo "WARN: log file missing — audit cron may not have run yet"
                                            exit 0
                                        fi

                                        LAST=\\$(grep "audit end" "\\$AUDIT_LOG" | tail -1)
                                        if [ -z "\\$LAST" ]; then
                                            echo "FAIL: no completed audit runs recorded yet"
                                            tail -20 "\\$AUDIT_LOG"
                                            exit 1
                                        fi

                                        # Format: "=== 2026-04-25T04:00:01Z audit end (rc=0) ==="
                                        LAST_TIME=\\$(echo "\\$LAST" | sed -E 's/=== ([^ ]+) audit end.*/\\\\1/')
                                        LAST_EPOCH=\\$(date -d "\\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                        AGE=\\$(( (\\$(date +%s) - LAST_EPOCH) / 60 ))

                                        echo "Last audit end: \\$LAST_TIME (\\$AGE min ago)"
                                        if [ "\\$AGE" -gt "\\$STALE_MINUTES" ]; then
                                            echo "FAIL: stale (>\\$STALE_MINUTES min)"
                                            tail -20 "\\$AUDIT_LOG"
                                            exit 1
                                        fi
                                        echo "OK"
                                    """
                                }
                            }
                        }

                        // Runs on the pickipedia VPS rather than here, so the
                        // signal comes over HTTP instead of off a local log.
                        // A held run — the change budget refusing an unusually
                        // large edit — is deliberately not a failure here; it
                        // wants a person to look, and it will keep saying so
                        // until one does.
                        stage('pickipedia podcast import') {
                            steps {
                                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                                    sh """
                                        STATUS_URL="https://pickipedia.xyz/status/podcast-import.txt"
                                        STALE_HOURS=30

                                        echo "=== pickipedia podcast import ==="
                                        BODY=\$(curl -fsS --max-time 30 "\$STATUS_URL" 2>&1) || {
                                            echo "WARN: no status yet at \$STATUS_URL"
                                            echo "      (expected until the import has run once)"
                                            exit 0
                                        }
                                        echo "\$BODY"

                                        LAST_TIME=\$(echo "\$BODY" | sed -n 's/^run: //p' | tail -1)
                                        if [ -z "\$LAST_TIME" ]; then
                                            echo "FAIL: status file has no run timestamp"
                                            exit 1
                                        fi
                                        LAST_EPOCH=\$(date -d "\$LAST_TIME" +%s 2>/dev/null || echo 0)
                                        AGE_H=\$(( (\$(date +%s) - LAST_EPOCH) / 3600 ))
                                        echo "Last run: \$LAST_TIME (\$AGE_H h ago)"

                                        if [ "\$AGE_H" -gt "\$STALE_HOURS" ]; then
                                            echo "FAIL: stale (>\$STALE_HOURS h) — the daily import has not run"
                                            exit 1
                                        fi

                                        case "\$BODY" in
                                            *"result: OK"*)
                                                echo "OK" ;;
                                            *"result: HELD"*)
                                                echo "FAIL: held for review — more pages would change than the"
                                                echo "      budget allows. This is the guard working. Look at the"
                                                echo "      breakdown above, and if it is intended, run the import"
                                                echo "      by hand with a raised --max-changes."
                                                exit 1 ;;
                                            *)
                                                echo "FAIL: import did not report success"
                                                exit 1 ;;
                                        esac
                                    """
                                }
                            }
                        }
                    }

                    post {
                        always {
                            script {
                                if (currentBuild.result == 'FAILURE') {
                                    echo "One or more cron checks STALE — see failed stages above."
                                } else {
                                    echo "All cron checks healthy."
                                }
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    triggers {
        cron('*/5 * * * *')
    }
}
