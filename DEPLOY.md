# Deployment Guide — Delta Basket Platform

## Prerequisites

- Docker & Docker Compose installed
- DigitalOcean account with a new droplet (Ubuntu 22.04, 2GB RAM minimum)
- Static IPv4 address assigned to the droplet
- Delta Exchange API key with Trading + Read Data permissions (IP whitelisted)

## Quick Start (Local)

```bash
# Clone the repo
git clone <repo-url>
cd delta-basket-platform

# Create .env from example
cp .env.example .env

# Edit .env with your credentials
nano .env

# Build and start
docker-compose up --build
```

Access the platform at `http://localhost:8000`

## Production Deployment (VPS)

### 1. SSH into your DigitalOcean droplet

```bash
ssh root@your_droplet_ip
```

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
usermod -aG docker root
```

### 3. Clone the repository

```bash
git clone <repo-url> /opt/delta-basket-platform
cd /opt/delta-basket-platform
```

### 4. Configure environment

```bash
cp .env.example .env
nano .env
```

Edit with:
- Your Delta API credentials
- Database password (strong, random)
- ENVIRONMENT=production
- DEBUG=false

### 5. Start the platform

```bash
docker-compose up -d
```

### 6. Verify deployment

```bash
# Check logs
docker-compose logs -f app

# Health check
curl http://localhost:8000/health

# List active containers
docker-compose ps
```

### 7. Access the dashboard

Open in your browser:
```
http://your_droplet_ip:8000
```

## Maintenance

### View logs
```bash
docker-compose logs -f app
docker-compose logs -f postgres
```

### Stop the platform
```bash
docker-compose down
```

### Restart
```bash
docker-compose restart
```

### Database backup
```bash
docker-compose exec postgres pg_dump -U postgres delta_basket_db > backup.sql
```

## Nginx Reverse Proxy (Optional)

For IP whitelisting and SSL:

```bash
apt-get install -y nginx certbot python3-certbot-nginx
```

Configure `/etc/nginx/sites-available/default`:

```nginx
server {
    listen 80;
    server_name _;
    
    # IP whitelist (restrict to your IPs)
    allow YOUR_IP;
    deny all;
    
    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Enable and restart:
```bash
systemctl enable nginx
systemctl restart nginx
```

## Monitoring

The platform includes health checks:
```bash
docker-compose ps
```

Expected output:
- `app ... Up (healthy)` — API running
- `postgres ... Up (healthy)` — Database running

## Troubleshooting

### Database connection error
```bash
docker-compose logs postgres
docker-compose restart postgres
```

### Out of disk space
```bash
docker system prune -a
```

### API not responding
```bash
docker-compose restart app
curl http://localhost:8000/health
```

## Security Checklist

- [ ] .env file has strong database password
- [ ] Delta API key is whitelisted to your droplet's IP
- [ ] Firewall blocks all ports except 80/443 (or 8000 if no proxy)
- [ ] Regular database backups scheduled
- [ ] Logs monitored for errors
- [ ] System kept updated: `apt-get update && apt-get upgrade`

## Support

For issues:
1. Check logs: `docker-compose logs app`
2. Verify health: `curl http://localhost:8000/health`
3. Confirm environment: `docker-compose exec app env`
