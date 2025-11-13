pipelineJob('backup-hunter-db') {
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Pull database backup from hunter') {
                            steps {
                                sh """
                                    # Create backup directory on maybelle
                                    mkdir -p /var/jenkins_home/hunter-db-backups

                                    # Pull latest backup from hunter
                                    scp root@hunter.cryptograss.live:/var/backups/magenta/latest.dump \\
                                        /var/jenkins_home/hunter-db-backups/magenta_\$(date +%Y%m%d_%H%M%S).dump

                                    # Create latest symlink
                                    ln -sf /var/jenkins_home/hunter-db-backups/magenta_\$(date +%Y%m%d_%H%M%S).dump \\
                                        /var/jenkins_home/hunter-db-backups/latest.dump

                                    # Keep only last 30 days
                                    find /var/jenkins_home/hunter-db-backups -name "magenta_*.dump" -mtime +30 -delete

                                    # List available backups
                                    echo "Available backups:"
                                    ls -lh /var/jenkins_home/hunter-db-backups/*.dump
                                """
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
    triggers {
        cron('0 3 * * *')  // Run daily at 3am (1 hour after hunter creates backup)
    }
}
