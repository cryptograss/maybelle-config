pipelineJob('magenta-memory-uptime') {
    description('''Health check for the magenta memory stack — MCP query server, Memory Lane read API, and watcher ingest.

Runs every 15 minutes, alerts after 2 consecutive failures.

Exists because the MCP server went down and stayed down through four consecutive magent wakeups without anyone noticing. Its failure is silent: magent bootstraps degraded and carries on, so nothing surfaces unless someone reads the wakeup message carefully.''')
    logRotator {
        numToKeep(100)
        daysToKeep(7)
    }
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                    }

                    environment {
                        FAILURE_COUNT_FILE = '/var/jenkins_home/magenta-memory-uptime-failures.txt'
                        ALERT_THRESHOLD = '2'
                        ALERT_EMAILS = 'justin@cryptograss.live,sky@cryptograss.live,rj@cryptograss.live'
                    }

                    stages {
                        stage('Run health checks') {
                            steps {
                                script {
                                    // `|| true` so a failing check still yields parseable
                                    // JSON. Letting the non-zero exit throw here would
                                    // lose the per-check detail exactly when it matters.
                                    def result = sh(
                                        script: '/var/jenkins_home/scripts/test-magenta-memory.py --json || true',
                                        returnStdout: true
                                    ).trim()

                                    // ...but `|| true` also swallows the script failing to
                                    // run at all, and readJSON's complaint about empty text
                                    // says nothing about why. Name that case before parsing.
                                    if (!result) {
                                        error("FAIL: health check produced no output — the script did not run. " +
                                              "Expected it at /var/jenkins_home/scripts/test-magenta-memory.py; " +
                                              "ansible copies it there from maybelle/scripts/.")
                                    }

                                    def json = readJSON text: result
                                    echo "Checks passed: ${json.passed}/${json.total}"

                                    json.checks.each { check ->
                                        def status = check.passed ? '✓' : '✗'
                                        def time = check.response_time_ms ? " (${check.response_time_ms.toInteger()}ms)" : ''
                                        echo "${status} ${check.name}: ${check.message}${time}"
                                    }

                                    if (!json.all_passed) {
                                        def failed = json.checks.findAll { !it.passed }.collect { it.name }
                                        error("FAIL: Checks failed: ${failed.join(', ')}")
                                    }

                                    echo "All health checks passed"
                                }
                            }
                        }
                    }

                    post {
                        failure {
                            script {
                                echo "=== MAGENTA MEMORY IS DEGRADED ==="
                                echo "Recovery: ssh maybelle, then 'docker logs mcp-server --tail 100'."
                                echo "A crash loop and a stopped container both look like connection refused."

                                def failureCount = 1
                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    failureCount = countStr.isInteger() ? countStr.toInteger() + 1 : 1
                                }

                                writeFile file: env.FAILURE_COUNT_FILE, text: failureCount.toString()
                                echo "Consecutive failures: ${failureCount}"

                                if (failureCount == env.ALERT_THRESHOLD.toInteger()) {
                                    echo "=== ALERT THRESHOLD REACHED ==="
                                    echo "magenta memory has been degraded for ${failureCount} consecutive checks."
                                    echo "Alert emails would go to: ${env.ALERT_EMAILS}"
                                    echo "Email sending not yet configured - see GitHub issue #35"
                                } else if (failureCount > env.ALERT_THRESHOLD.toInteger()) {
                                    echo "Still degraded (${failureCount} failures). Alert already sent at threshold."
                                }
                            }
                        }
                        success {
                            script {
                                echo "magenta memory health check passed"

                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    def prevFailures = countStr.isInteger() ? countStr.toInteger() : 0

                                    if (prevFailures >= env.ALERT_THRESHOLD.toInteger()) {
                                        echo "=== RECOVERY ==="
                                        echo "magenta memory is back UP after ${prevFailures} failures"
                                    }

                                    sh 'rm -f /var/jenkins_home/magenta-memory-uptime-failures.txt'
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
        // Every 15 minutes. The failure this catches lasted days, so a tighter
        // poll buys nothing and just adds noise.
        cron('*/15 * * * *')
    }
}
