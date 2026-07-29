pipelineJob('pickipedia-build') {
    properties {
        disableConcurrentBuilds()
    }
    logRotator {
        numToKeep(30)
        daysToKeep(14)
    }
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url('https://github.com/cryptograss/pickipedia.git')
                        credentials('github-token')
                    }
                    branch('*/production')
                }
            }
            scriptPath('Jenkinsfile')
        }
    }
    triggers {
        // Poll every 5 minutes for changes
        cron('*/5 * * * *')
    }
}
