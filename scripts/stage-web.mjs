/** Copy the original SPPS web app to a Capacitor webDir without changing source. */
import { cpSync, copyFileSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const source=join(root,'web');
const target=join(root,'www');
if(!existsSync(join(source,'index.html')) || !existsSync(join(source,'app.js'))) throw Error('Original SPPS web source missing');
rmSync(target,{recursive:true,force:true});
mkdirSync(join(target,'assets'),{recursive:true});
// FastAPI uses /assets while Capacitor requires index.html in webDir.
for(const f of ['index.html','room.html','privacy.html','terms.html','portal.html','your-data.html','athlete.html','find.html','policies.html','guide.html'])copyFileSync(join(source,f),join(target,f));
// The brand logo is referenced as /assets/spps-v1-logo.png by the UI; without it the Android app shows a broken image.
for(const f of ['app.js','v2.js','athlete.js','find.js','room.js','styles.css','portal.js','push.js','spps-v1-logo.png']){
  if(!existsSync(join(source,f))) throw Error('Required web asset missing: '+f);
  copyFileSync(join(source,f),join(target,'assets',f));
}
copyFileSync(join(source,'sw.js'),join(target,'sw.js')); // service worker, served from the site root
cpSync(join(source,'avatars'),join(target,'assets','avatars'),{recursive:true});
console.log('Staged original web UI and static assets to www/');
