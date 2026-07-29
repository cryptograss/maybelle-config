pipelineJob('arthel-fetch-chain-data') {
    properties {
        disableConcurrentBuilds()
    }
    logRotator {
        numToKeep(50)
        daysToKeep(7)
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
            scriptPath('integration/Jenkinsfile-fetch-chain-data')
        }
    }
    triggers {
        // Was every odd minute (30 runs/hour). ENS resolution in
        // chain_reading.js hits mainnet once per NFT owner without caching,
        // and running every 2 min was burning through Alchemy compute-unit
        // quota (multicall3 traffic to 0xca11bde... on mainnet). NFT
        // ownership doesn't move fast enough to justify sub-15-minute
        // refresh; downstream pickipedia-import-bluerailroad still runs
        // every 2 min but reads whatever chain_data is on disk, so freshness
        // there is unchanged from its POV.
        cron('*/15 * * * *')  // Every 15 minutes (:00, :15, :30, :45)
    }
}