pipelineJob('arthel-fetch-pinata-data') {
    description('Fetch all pins from Pinata API and save to shared location. Runs every even minute to keep IPFS status page fresh.')

    properties {
        disableConcurrentBuilds()
    }
    logRotator {
        numToKeep(30)
        daysToKeep(7)
    }
    definition {
        cpsScm {
            scm {
                git {
                    remote {
                        // githouse's copy (the flip, Cryptograss:Moods/ramp/Plan, section 6): production there
                        // moves only by a signed merge, so a push to GitHub's, around githouse, never builds here.
                        url('file:///srv/git/arthel.git')
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
