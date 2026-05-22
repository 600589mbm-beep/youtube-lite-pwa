module.exports = {
  apps: [
    {
      name: 'youtube-automation-agent',
      script: './server.js',
      instances: 1,
      exec_mode: 'fork',
      watch: false,
      autorestart: true,
      max_memory_restart: '300M',
      env: {
        NODE_ENV: 'production',
        PORT: process.env.PORT || 3456,
      },
      env_development: {
        NODE_ENV: 'development',
        PORT: process.env.PORT || 3456,
      },
    },
  ],
};
