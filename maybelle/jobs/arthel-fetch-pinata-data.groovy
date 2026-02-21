pipelineJob('arthel-fetch-pinata-data') {
    description('Fetch all pins from Pinata API and save to shared location. Runs every even minute to keep IPFS status page fresh.')

    properties {
        disableConcurrentBuilds()
    }
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
            scriptPath('integration/Jenkinsfile-fetch-pinata-data')
        }
    }
    triggers {
        cron('*/2 * * * *')  // Run every 2 minutes
    }
}
