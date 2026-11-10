pipelineJob('arthel-production-build') {
    properties {
        disableConcurrentBuilds()
    }
    logRotator {
        numToKeep(30)
        daysToKeep(30)
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
            scriptPath('integration/Jenkinsfile')
        }
    }
    triggers {
        cron('*/2 * * * *')
    }
}