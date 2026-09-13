const { spawn } = require('child_process');
const path = require('path');

const electron = require('electron');
const root = path.resolve(__dirname, '..');
const args = [root];
if (process.platform === 'linux' && process.env.DISPLAY) args.push('--ozone-platform=x11');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, args, { cwd: root, env, stdio: 'inherit' });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
