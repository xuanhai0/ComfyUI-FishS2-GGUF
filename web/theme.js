/** Shared Fish S2 node styling and concise help. */
import { app } from '../../scripts/app.js';
const descriptions={
 S2ServerControl:'Server Control · Choose model and voice library. Monitor toggles independently of the server.',
 S2VoiceCreate:'Voice Create · Upload reference audio and transcript, then queue independently. Existing voices are protected.',
 S2VoiceInput:'Voice Input · Reference audio or cached .s2voice; connected inputs override built-in voice selections.',
 FishS2Generate:'Generate · Use [speakerN] tags to assign voices. The speaker slider controls active voices; green segments mean WAV received.'
};
app.registerExtension({name:'FishS2.SharedTheme',async beforeRegisterNodeDef(nodeType,nodeData){
 const help=descriptions[nodeData.name];if(!help)return;
 const old=nodeType.prototype.onNodeCreated;
 nodeType.prototype.onNodeCreated=function(...args){
  const result=old?.apply(this,args);if(nodeData.name!=='S2ServerControl')this.color='#292d46';this.bgcolor='#171b2c';
  if(nodeData.name==='FishS2Generate'||nodeData.name==='S2ServerControl')return result;
  // Native widgets only: no additional DOM help panels.
  return result;
 };
}});
