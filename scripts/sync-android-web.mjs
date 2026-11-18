/** Copy the staged www/ web assets into the Android app (the web part of `npx cap sync android`).
 *  Used where the Capacitor CLI is not installed; native files and package ids are never touched. */
import { cpSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const www=join(root,'www'), target=join(root,'android','app','src','main','assets','public');
if(!existsSync(join(www,'index.html'))) throw Error('Run npm run web:stage first');
if(!existsSync(target)) throw Error('Android web asset folder missing');
cpSync(www,target,{recursive:true});
console.log('Copied www/ into android/app/src/main/assets/public');
