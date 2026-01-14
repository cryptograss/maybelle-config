pipelineJob('pickipedia-import-bluerailroad') {
    description('Import Blue Railroad token data from chain-data into PickiPedia wiki pages. Triggered after pickipedia deploys.')

    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    options {
                        buildDiscarder(logRotator(numToKeepStr: '30'))
                    }

                    stages {
                        stage('Run import on NFS') {
                            steps {
                                script {
                                    def result = sh(
                                        script: """
                                            ssh nfs-pickipedia 'cd ~/public && php extensions/BlueRailroadIntegration/maintenance/importBlueRailroads.php 2>&1'
                                        """,
                                        returnStdout: true
                                    ).trim()

                                    echo "=== Import Output ==="
                                    echo result

                                    // Check for errors in output
                                    if (result.contains('Error') || result.contains('Fatal')) {
                                        error("Import script reported errors")
                                    }
                                }
                            }
                        }
                    }

                    post {
                        success {
                            echo "Blue Railroad import completed successfully"
                        }
                        failure {
                            echo "=== IMPORT FAILED ==="
                            echo "Check the import script and chain-data availability"
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }

    // No automatic trigger - triggered by deploy script after successful pickipedia deploy
}
