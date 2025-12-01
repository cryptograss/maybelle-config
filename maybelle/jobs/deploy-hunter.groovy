pipelineJob('deploy-hunter') {
    description('Hunter deployment log - triggered externally via deploy-hunter-remote.py')

    parameters {
        stringParam('DEPLOY_USER', '', 'User who initiated the deploy')
        stringParam('DEPLOY_STATUS', '', 'success or failure')
        stringParam('DEPLOY_DURATION', '', 'Duration in seconds')
        textParam('DEPLOY_LOG', '', 'Ansible output log')
    }

    definition {
        cps {
            script('''
                pipeline {
                    agent any
                    stages {
                        stage('Deploy Report') {
                            steps {
                                script {
                                    def status = params.DEPLOY_STATUS ?: 'unknown'
                                    def user = params.DEPLOY_USER ?: 'unknown'
                                    def duration = params.DEPLOY_DURATION ?: 'unknown'

                                    echo "=== Hunter Deployment Report ==="
                                    echo ""
                                    echo "User: ${user}"
                                    echo "Status: ${status}"
                                    echo "Duration: ${duration} seconds"
                                    echo ""
                                    echo "=== Ansible Output ==="
                                    echo params.DEPLOY_LOG ?: '(no log provided)'

                                    if (status == 'failure') {
                                        error("Deployment failed")
                                    }
                                }
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
}
