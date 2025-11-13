# Maybelle Deployment

Infrastructure for deploying Jenkins CI/CD for cryptograss project to maybelle server.

## Deployment

From maybelle server:
```bash
cd ~/maybelle-config/maybelle
ansible-playbook -i localhost, ansible/maybelle.yml --ask-vault-pass
```

This deploys:
- Jenkins in Docker container
- Nginx reverse proxy with SSL
- Automated builds for production and PRs
- GitHub integration

## Services

- **Jenkins**: https://maybelle.cryptograss.live
- **Admin Login**: See vault for credentials

## Configuration

- `ansible/maybelle.yml` - Main playbook
- `jenkins-docker/Dockerfile` - Custom Jenkins image
- `configs/jenkins.yml` - Jenkins CasC configuration
- `jobs/*.groovy` - Jenkins job definitions
