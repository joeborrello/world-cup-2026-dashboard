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
      // Refresh scores/standings/bracket every 15 minutes from openfootball
      // (and football-data.org if a key is set above).
      name: 'worldcup-2026-updater',
      script: '/var/www/worldcup-2026/update_results.py',
      interpreter: '/var/www/worldcup-2026/venv/bin/python',
      cwd: '/var/www/worldcup-2026',
      cron_restart: '*/15 * * * *',
      autorestart: false,
    },
    {
      // Publish a small live-data snapshot to docs/data/live.json and push it
      // to GitHub so the Pages landing site stays current. Runs a few minutes
      // behind the updater; only commits when the content actually changed.
      name: 'worldcup-2026-pages',
      script: '/var/www/worldcup-2026/publish_pages.py',
      interpreter: '/var/www/worldcup-2026/venv/bin/python',
      cwd: '/var/www/worldcup-2026',
      cron_restart: '3,18,33,48 * * * *',
      autorestart: false,
    },
  ],
};
