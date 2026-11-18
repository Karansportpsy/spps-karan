/** Only configure a local debug WebView URL or fall back to bundled web files. */
import {writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
export function buildConfig(mode,url){
  const config={appId:'com.winmindperform.spps.beta15',appName:'SPPS Free 1.0',webDir:'www',server:{androidScheme:'https'},plugins:{SystemBars:{insetsHandling:'css',initialViewportFitValueHint:'cover',style:'LIGHT'}}};
  if(mode==='bundle')return config;
  if(mode==='hosted-beta'){
    if(!url)throw Error('SPPS_ANDROID_DEV_URL must point to your hosted HTTPS FastAPI URL');
    let hosted;try{hosted=new URL(url)}catch{throw Error('Invalid hosted URL')}
    if(hosted.protocol!=='https:' || hosted.username || hosted.password || hosted.search || hosted.hash || hosted.pathname!=='/' || !hosted.hostname || hosted.hostname==='localhost' || hosted.hostname==='127.0.0.1')throw Error('Hosted beta requires an external HTTPS origin without credentials, path, query or fragment');
    config.server={url:hosted.origin+'/studio',androidScheme:'https'};
    return config;
  }
  if(mode!=='debug')throw Error('Only debug, hosted-beta or bundle mode is supported; a Play release is not approved.');
  const address=url||'http://localhost:8000/studio';
  let parsed;try{parsed=new URL(address)}catch{throw Error('Invalid dev server URL')}
  if(parsed.username||parsed.password||parsed.search||parsed.hash||!['/','/studio'].includes(parsed.pathname)) throw Error('Use only a server origin, without credentials, path or query');
  if(parsed.protocol!=='http:' && parsed.protocol!=='https:')throw Error('Only http/https origins supported');
  // Development over unencrypted HTTP is only permitted on the device loopback.
  if(parsed.protocol==='http:' && parsed.hostname!=='localhost' && parsed.hostname!=='127.0.0.1')throw Error('HTTP is restricted to localhost. Use a trusted HTTPS origin for non-loopback devices.');
  config.server={url:parsed.origin+(parsed.pathname==='/'?'':parsed.pathname),androidScheme:'https',...(parsed.protocol==='http:'?{cleartext:true}:{})};
  return config;
}
const executing=process.argv[1] && resolve(process.argv[1])===fileURLToPath(import.meta.url);
if(executing){
  try{
    const result=buildConfig(process.argv[2]||'debug',process.env.SPPS_ANDROID_DEV_URL);
    writeFileSync(resolve(root,'capacitor.config.json'),JSON.stringify(result,null,2)+'\n');
    console.log('Capacitor mode:',process.argv[2]||'debug','server:',result.server.url||'(bundled assets only)');
  }catch(error){console.error(error.message);process.exit(1)}
}
