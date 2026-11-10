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
                        // githouse's copy (the flip, Cryptograss:Moods/ramp/Plan, section 6): production there
                        // moves only by a signed merge, so a push to GitHub's, around githouse, never builds here.
                        url('file:///srv/git/pickipedia.git')
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
