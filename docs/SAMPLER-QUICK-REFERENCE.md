# Firewalla sampler Operations Quick Reference

Use the following commands from a terminal under the M4 LaunchAgent account. See the [Operator Guide](SAMPLER-OPERATOR-GUIDE.md) for installation prerequisites, limits and the local provider contract.

```sh
sampler_dir="$HOME/Library/Application Support/Firewalla Health Sampler"
job="gui/$(id -u)/com.nibrwr.firewalla-security-sampler"
launchctl print "$job"
/opt/homebrew/bin/python3 -E -s -B "$sampler_dir/program/firewalla-health.py" status
```

| Result | What to check |
| --- | --- |
| `health.state=observed` | The timestamp should advance every five minutes while the M4 is awake and logged in. `reads_reserved_today` counts attempts. |
| `health.state=unknown` | Check the fixed reason and the last accepted time. Ask the credential owner to check the provider and the intended paired box if the endpoint or identity is unavailable. |
| `gap_active=true` | Two missed intervals opened a gap. Confirm that two fresh samples recover it after the cause is repaired. |
| `possible_process_restart` | Check the owner's appliance evidence if the cause matters. Uptime tracks the control process. |
| `overflow_today>0` | New incidents exceeded the UTC daily quota. Treat the retained history as partial. |
| State or write failure | Check job exit status, directory ownership and free space. Preserve the database and any journal during diagnosis. Check integrity on an offline copy after the job is unloaded. |
| `observer_context.state=unknown` | Check the existing Observer read-only view. It is independent of the Firewalla health read. |

## Roll back the sampler

Use the authorized rollback for the new job only. Keep its state directory.

```sh
label=com.nibrwr.firewalla-security-sampler
domain="gui/$(id -u)"
agent="$HOME/Library/LaunchAgents/$label.plist"
sampler_dir="$HOME/Library/Application Support/Firewalla Health Sampler"

launchctl disable "$domain/$label"
launchctl bootout "$domain/$label"
launchctl print "$domain/$label"
```

Proceed when the job is absent. Verify that `plutil -extract Label raw -o - "$agent"` returns the exact label before removing that plist. Move the sampler's `program` directory out of the execution path or remove that verified program copy. Keep `state` for recovery evidence subject to the retention policy. Confirm the existing connector's health read and two-tool inventory, Observer's four responding targets, and the disabled state of the historical collector and bridge.
