pipelineJob('pickipedia-uptime') {
    description('HTTP health check for PickiPedia production (pickipedia.xyz) - runs every minute. Alerts after 2 consecutive failures.')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        disableConcurrentBuilds()
                    }

                    environment {
                        FAILURE_COUNT_FILE = '/var/jenkins_home/pickipedia-uptime-failures.txt'
                        ALERT_THRESHOLD = '2'
                        ALERT_EMAILS = 'justin@cryptograss.live,sky@cryptograss.live,rj@cryptograss.live'
                    }

                    stages {
                        stage('Check HTTP status') {
                            steps {
                                script {
                                    def httpCode = sh(script: """
                                        curl -s -o /dev/null -w "%{http_code}" --max-time 30 "https://pickipedia.xyz/wiki/Main_Page"
                                    """, returnStdout: true).trim()

                                    echo "HTTP Status: ${httpCode}"

                                    if (httpCode == '200') {
                                        echo "OK: PickiPedia is UP"
                                    } else {
                                        error("FAIL: PickiPedia returned HTTP ${httpCode}")
                                    }
                                }
                            }
                        }

                        stage('Check API endpoint') {
                            steps {
                                script {
                                    def apiCode = sh(script: """
                                        curl -s -o /dev/null -w "%{http_code}" --max-time 30 "https://pickipedia.xyz/api.php?action=query&meta=siteinfo&format=json"
                                    """, returnStdout: true).trim()

                                    echo "API Status: ${apiCode}"

                                    if (apiCode == '200') {
                                        echo "OK: MediaWiki API responding"
                                    } else {
                                        echo "WARNING: API returned HTTP ${apiCode}"
                                    }
                                }
                            }
                        }
                    }

                    post {
                        failure {
                            script {
                                echo "=== PICKIPEDIA IS DOWN ==="

                                // Read current failure count
                                def failureCount = 1
                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    failureCount = countStr.isInteger() ? countStr.toInteger() + 1 : 1
                                }

                                // Write updated count
                                writeFile file: env.FAILURE_COUNT_FILE, text: failureCount.toString()
                                echo "Consecutive failures: ${failureCount}"

                                // Check if we should alert
                                if (failureCount == env.ALERT_THRESHOLD.toInteger()) {
                                    echo "=== ALERT THRESHOLD REACHED ==="
                                    echo "PickiPedia has been down for ${failureCount} consecutive checks."
                                    echo "Alert emails would go to: ${env.ALERT_EMAILS}"
                                    echo "Email sending not yet configured - see GitHub issue #35"
                                } else if (failureCount > env.ALERT_THRESHOLD.toInteger()) {
                                    echo "Still down (${failureCount} failures). Alert already sent at threshold."
                                }
                            }
                        }
                        success {
                            script {
                                echo "PickiPedia health check passed"

                                // Check if we were previously down and should send recovery alert
                                if (fileExists(env.FAILURE_COUNT_FILE)) {
                                    def countStr = readFile(env.FAILURE_COUNT_FILE).trim()
                                    def prevFailures = countStr.isInteger() ? countStr.toInteger() : 0

                                    if (prevFailures >= env.ALERT_THRESHOLD.toInteger()) {
                                        echo "=== RECOVERY ==="
                                        echo "PickiPedia is back UP after ${prevFailures} failures"
                                    }

                                    // Reset failure count
                                    sh 'rm -f /var/jenkins_home/pickipedia-uptime-failures.txt'
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
        cron('* * * * *')  // Run every minute
    }
}
