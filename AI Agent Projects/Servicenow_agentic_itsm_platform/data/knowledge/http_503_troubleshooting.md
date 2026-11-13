# HTTP 503 service unavailable troubleshooting

Symptoms: users receive HTTP 503 responses or service instances are unavailable.

Investigation:
- Check application instance health and load balancer target health.
- Review application and reverse proxy logs.
- Check dependency availability, connection pools and resource saturation.
- Review recent deployment and configuration changes.

Resolution options:
- Restore unhealthy service instances.
- Correct dependency or capacity issues.
- Roll back a confirmed faulty deployment with approval.
- Verify endpoint health and user access.
