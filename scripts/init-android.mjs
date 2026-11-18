/** Create/synchronize the native Android project without Windows cmd shims or a shell.
 *
 * Node's process.execPath points to the actual node.exe on Windows. The locally
 * installed Capacitor CLI is resolved via its package.json `bin` metadata, so
 * the setup never relies on `node.cmd`, `npx.cmd`, PATH or shell:true.
 */
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));

function runJavaScript(entry, args = []) {
  const result = spawnSync(process.execPath, [entry, ...args], {
    cwd: root,
    stdio: 'inherit',
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${entry} ${args.join(' ')} failed (${result.status ?? result.error?.message ?? result.signal})`);
  }
}

function locallyInstalledCapacitor() {
  const manifest = resolve(root, 'node_modules/@capacitor/cli/package.json');
  if (!existsSync(manifest)) {
    throw new Error('Capacitor CLI is missing. Run npm install in this project folder first.');
  }
  const packageInfo = JSON.parse(readFileSync(manifest, 'utf8'));
  const entryPoint = typeof packageInfo.bin === 'string'
    ? packageInfo.bin
    : (packageInfo.bin?.cap ?? packageInfo.bin?.capacitor);
  if (!entryPoint || typeof entryPoint !== 'string') {
    throw new Error('Installed Capacitor CLI does not expose its expected cap executable.');
  }
  const cli = resolve(dirname(manifest), entryPoint);
  if (!existsSync(cli)) {
    throw new Error(`Capacitor CLI entry point is missing: ${cli}. Reinstall with npm install.`);
  }
  return cli;
}

try {
  const cap = locallyInstalledCapacitor();
  runJavaScript(resolve(root, 'scripts/stage-web.mjs'));
  if (!existsSync(resolve(root, 'android/settings.gradle'))) {
    runJavaScript(cap, ['add', 'android']);
  }
  runJavaScript(cap, ['sync', 'android']);
  runJavaScript(resolve(root, 'scripts/apply-native-overrides.mjs'));
  console.log('Android Studio project ready in android/. Android SDK and Gradle are required to compile.');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
