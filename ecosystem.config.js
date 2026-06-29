module.exports = {
  apps: [
    {
      name: 'worldcup-2026',
      script: '/var/www/worldcup-2026/venv/bin/gunicorn',
      args: '-w 1 --threads 8 -b 127.0.0.1:5010 --timeout 60 '
          + '--access-logfile /var/www/worldcup-2026/access.log '
          + '--error-logfile /var/www/worldcup-2026/error.log app:app',
      cwd: '/var/www/worldcup-2026',
      interpreter: 'none',
      env: {
        // Optional: set a football-data.org key here to enable live enrichment.
        // FOOTBALL_DATA_API_KEY: '...',
      },
    },
    {
      // Refresh scores/standings/bracket every ~7 minutes from openfootball
      // (and football-data.org if a key is set above).
      name: 'worldcup-2026-updater',
      script: '/var/www/worldcup-2026/update_results.py',
      interpreter: '/var/www/worldcup-2026/venv/bin/python',
      cwd: '/var/www/worldcup-2026',
      cron_restart: '*/7 * * * *',
      autorestart: false,
    },
    {
      // Auto-deploy: every ~5 minutes, fast-forward the live checkout to
      // origin/main and restart the web app if anything new landed. This is the
      // safety net for "merged to main but nobody deployed it" — the failure
      // mode that kept the Golden Boot tracker (JOE-13) off the live app twice.
      // It is a no-op when already current and only ever fast-forwards.
      name: 'worldcup-2026-deploy',
      script: '/var/www/worldcup-2026/deploy.py',
      interpreter: '/var/www/worldcup-2026/venv/bin/python',
      cwd: '/var/www/worldcup-2026',
      cron_restart: '*/5 * * * *',
      autorestart: false,
    },
    // The Pages landing site now polls the droplet's /api/landing endpoint
    // directly, so the old git-push snapshot publisher (worldcup-2026-pages,
    // publish_pages.py) has been retired — no more churn commits on main.
    // docs/data/live.json is kept only as an offline fallback.
  ],
};
