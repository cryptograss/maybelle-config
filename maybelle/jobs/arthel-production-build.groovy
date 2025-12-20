pipelineJob('arthel-production-build') {
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        url('https://github.com/cryptograss/justinholmes.com.git')
                        credentials('github-token')
                    }
                    branch('*/production')
                }
            }
            scriptPath('integration/Jenkinsfile')
        }
    }
    triggers {
        cron('*/2 * * * *')
    }
}