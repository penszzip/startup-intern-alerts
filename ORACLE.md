# Deploying on Oracle Cloud Always Free

Gives you a real 10-minute cron with no drift, free forever, on a VM you control.
Unlike GitHub Actions it needs a credit card for identity verification (it is not
charged for Always Free resources).

## 1. Create the VM

Sign up at [cloud.oracle.com](https://cloud.oracle.com), then **Compute → Instances →
Create instance**:

| Setting | Choose |
|---|---|
| Image | **Ubuntu 24.04** (ships Python 3.12) |
| Shape | **VM.Standard.E2.1.Micro** (AMD, 1/8 OCPU, 1 GB) |
| SSH keys | upload your public key, or let it generate one — **save the private key** |

**Pick the AMD micro shape, not the ARM Ampere A1.** Ampere gives far more resource
(4 cores / 24 GB) but is chronically capacity-constrained — "Out of host capacity" is
the single most common thing that stops this deployment. This workload is a Python
script that runs for 30 seconds every 10 minutes; 1 GB of AMD is ample.

No ingress rules are needed. Everything here is outbound only.

## 2. Two traps worth knowing before you start

**Idle reclamation.** Oracle reclaims Always Free *compute* instances it considers
idle — roughly, under 20% CPU over 7 days. A cron job that sleeps most of the time is
exactly that profile, so this deployment is a plausible reclamation target. The fix is
free: upgrade the account to **Pay As You Go**. Always Free resources stay free, and
upgraded accounts are exempt from idle reclamation. Do this before you rely on it.

**Outbound SMTP.** Oracle blocks port 25 by default on tenancies created after June
2021. Ports 465 and 587 are authenticated submission ports and are normally allowed —
this project uses 465 and falls back to 587 automatically. `preflight.py` tests both
before you trust anything. If both are blocked, raise a service-limit request.

## 3. Deploy

```bash
ssh -i /path/to/private.key ubuntu@<VM_PUBLIC_IP>

sudo apt-get update -qq && sudo apt-get install -y -qq git
git clone https://github.com/<you>/startup-intern-alerts.git
cd startup-intern-alerts

./deploy/install.sh
```

`install.sh` is idempotent. It will:

1. verify Python 3.9+
2. prompt for your Gmail address and app password, write `secrets.json`, `chmod 600`
3. run **preflight** — outbound HTTPS to all three ATS platforms, the startup.jobs MCP
   endpoint, and SMTP auth on 465 and 587 — and **refuse to install timers if that
   fails**, so you find problems now rather than from silence at 3am
4. install and enable both systemd timers
5. seed state so you are not emailed the entire existing backlog on first run

If you *want* the backlog, delete `ats_state.json` and `state.json` before step 3.

## 4. Verify

```bash
python3 deploy/preflight.py --send     # sends a real test email
systemctl list-timers 'intern-alerts-*'
journalctl -u intern-alerts-realtime -f
sudo systemctl start intern-alerts-realtime.service   # run immediately
```

## 5. Turn off the Windows tasks

Otherwise both machines alert you, from separate state files:

```powershell
Disable-ScheduledTask -TaskName StartupInternAlerts
Disable-ScheduledTask -TaskName StartupInternAlerts-Realtime
```

## Why systemd timers rather than crontab

- `Persistent=true` — a run missed while the VM rebooted fires on next boot; cron just
  skips it silently.
- `journalctl -u intern-alerts-realtime` gives real logs with exit statuses. Cron's
  default is mailing root, which nothing reads.
- `RandomizedDelaySec=45` avoids hammering the ATS APIs on the exact tick.
- No PATH surprises — `ExecStart` is an absolute interpreter path.

Crontab equivalent, if you prefer it:

```cron
*/10 * * * * cd ~/startup-intern-alerts && /usr/bin/python3 atswatch.py
7,37 * * * * cd ~/startup-intern-alerts && /usr/bin/python3 main.py
```

## Changing the schedule

Edit `OnUnitActiveSec` in `/etc/systemd/system/intern-alerts-realtime.timer`, then:

```bash
sudo systemctl daemon-reload && sudo systemctl restart intern-alerts-realtime.timer
```

Going below ~5 minutes is not useful: postings do not appear that fast, and it only
increases load on the ATS APIs.

## Compatibility note

The code targets **Python 3.9+**. It originally used `datetime.UTC`, which is 3.11+
and would have crashed on Ubuntu 22.04's Python 3.10; that is now
`datetime.timezone.utc`. Only the stdlib is used, so there is nothing to `pip install`
and no virtualenv to maintain.
