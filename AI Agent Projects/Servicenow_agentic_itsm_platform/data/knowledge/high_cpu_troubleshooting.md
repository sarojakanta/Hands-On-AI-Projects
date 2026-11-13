# High CPU utilization troubleshooting

Symptoms: sustained CPU above 90%, slow application response and request timeouts.

Investigation:
- Identify the process consuming CPU.
- Compare CPU utilization with baseline and traffic volume.
- Review application logs for repeated errors or retry loops.
- Check recent releases, configuration changes and scheduled jobs.
- Inspect downstream dependencies.

Resolution options:
- Roll back a confirmed problematic deployment with approval.
- Correct runaway process or retry configuration.
- Scale capacity only after validating demand and architecture.
- Verify service health and monitor for recurrence.
