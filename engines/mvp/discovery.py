"""Bounded contextual discovery. DOM references stay server-side; AI sees fresh IDs.

Ported from BlindSpot/backend/app/services/browser/discovery.py. The JS payload
(HELPERS/EXTRACT/VALIDATE) is unchanged — it is the actual element-identity and
ambiguity logic, not a rewrite candidate. Only the imports change: engines/
cannot import app.*, so `origin` and the error type are the local equivalents.
"""
import json
from urllib.parse import urlsplit

from engines.mvp.errors import StaleElementError
from engines.mvp.policy import origin

MAX_SCAN = 3000
MAX_CONTROLS = 120
HELPERS = r"""
const SEL='input,button,select,textarea,a[href],[contenteditable="true"],[role="button"],[role="tab"],[role="menuitem"],[role="menuitemcheckbox"],[role="menuitemradio"],[role="checkbox"],[role="radio"],[role="switch"],[role="combobox"],[role="listbox"],[role="option"],[role="searchbox"],[role="slider"],[role="spinbutton"],[role="treeitem"]';
function customClick(el){
 if(el.matches(SEL)||!['DIV','SPAN','LI','ARTICLE','SECTION'].includes(el.tagName))return false;
 const click=typeof el.onclick==='function'||el.hasAttribute('onclick');
 const keyboard=el.tabIndex>=0&&(typeof el.onkeydown==='function'||typeof el.onkeyup==='function');
 if(!click&&!keyboard)return false;
 // Delegated roots and wrappers around real controls are not independent targets.
 if(el.querySelector(SEL))return false;
 if(Array.from(el.querySelectorAll('div,span,li,article,section')).some(child=>typeof child.onclick==='function'||child.hasAttribute('onclick')))return false;
 return true;
}
function candidateNodes(){
 const native=Array.from(document.querySelectorAll(SEL));
 const custom=Array.from(document.querySelectorAll('div,span,li,article,section')).slice(0,3000).filter(customClick);
 return [...native,...custom];
}
const squash=s=>(s||'').replace(/\s+/g,' ').trim();
const text=el=>el?squash(el.innerText||el.textContent):'';
const visible=el=>!!el&&el.isConnected&&el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden'&&getComputedStyle(el).display!=='none'&&getComputedStyle(el).opacity!=='0'&&!el.closest('[hidden],[inert],[aria-hidden="true"]');
const refs=(el,key)=>squash((el.getAttribute(key)||'').split(/\s+/).map(id=>text(document.getElementById(id))).join(' '));
const label=el=>squash(Array.from(el.labels||[]).map(node=>{const copy=node.cloneNode(true);copy.querySelectorAll('input,button,select,textarea').forEach(n=>n.remove());return text(copy)}).join(' '));
const named=el=>squash(el.getAttribute('aria-label'))||refs(el,'aria-labelledby');
function scopeName(el,role){
 let name=named(el)||el.getAttribute('data-testid');
 if(!name&&role==='row'){
  const cells=Array.from(el.querySelectorAll(':scope > th,:scope > td,:scope > [role="rowheader"],:scope > [role="cell"]'));
  name=cells.map(cell=>{const copy=cell.cloneNode(true);copy.querySelectorAll(SEL).forEach(n=>n.remove());return text(copy)}).find(Boolean)||'';
 }
 if(!name)name=text(el.querySelector('legend,caption,h1,h2,h3,h4,h5,h6'));
 return squash(name).slice(0,180);
}
function scopes(el){
 const result=[];
 for(let node=el.parentElement;node&&node!==document.body&&result.length<4;node=node.parentElement){
  const tag=node.tagName.toLowerCase(),role=node.getAttribute('role')||({tr:'row',form:'form',fieldset:'group',section:'region',article:'article',nav:'navigation',dialog:'dialog',table:'table'}[tag]);
  if(!['row','form','group','region','article','navigation','dialog','alertdialog','table'].includes(role))continue;
  const name=scopeName(node,role);
  if(name)result.push({role,name});
 }
 return result;
}
function inspect(el){
 if(!visible(el))return null;
 const tag=el.tagName.toLowerCase(),type=(el.getAttribute('type')||'text').toLowerCase(),auto=(el.getAttribute('autocomplete')||'').toLowerCase();
 if(tag==='input'&&(['hidden','password','email'].includes(type)||['username','current-password','new-password','email','one-time-code'].includes(auto)))return null;
 const custom=customClick(el);
 const labelled=label(el),name=named(el)||labelled||el.getAttribute('placeholder')||el.getAttribute('title')||(custom?(text(el.querySelector('h1,h2,h3,h4,h5,h6'))||text(el)):'')||(['button','a'].includes(tag)||el.getAttribute('role')==='button'?text(el):'');
 if(tag==='input'&&/^(password|email|e-mail|username|user name|one.time.code)$/i.test(squash(name)))return null;
 const role=(el.getAttribute('role')||({button:'button',a:'link',select:el.multiple?'listbox':'combobox',textarea:'textbox',input:({checkbox:'checkbox',radio:'radio',search:'searchbox',range:'slider',number:'spinbutton',submit:'button',button:'button'}[type]||'textbox')}[tag])||(custom?'button':el.isContentEditable?'textbox':'')).split(' ')[0];
 const test=el.getAttribute('data-testid')||el.getAttribute('data-test-id')||el.getAttribute('data-test');
 const locator=test?{test_id:test}:custom&&name?{custom_click:squash(name).slice(0,300)}:role&&name?{role,name:squash(name).slice(0,300)}:labelled?{label:labelled.slice(0,300)}:null;
 const popup=el.closest('dialog[open],[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"]');
 const scope=scopes(el),disabled=!!el.disabled||el.getAttribute('aria-disabled')==='true',readonly=!!el.readOnly||el.getAttribute('aria-readonly')==='true';
 const modals=Array.from(document.querySelectorAll('dialog[open],[role="dialog"][aria-modal="true"],[role="alertdialog"][aria-modal="true"]')).filter(visible);
 const blocked=modals.length>1||modals.length===1&&!modals[0].contains(el);
 let purpose=role==='link'?'navigation':['checkbox','radio','switch'].includes(role)?'toggle':['combobox','listbox','option'].includes(role)?'selection':['textbox','searchbox','spinbutton','slider'].includes(role)?'input':'action';
 if(type==='search'||/\b(search|filter|sort)\b/i.test(name))purpose='view_filter';
 else if(role==='button'&&/\b(save|update|apply|submit)\b/i.test(name))purpose='commit_candidate';
 const helper=refs(el,'aria-describedby')||text(el.parentElement?.querySelector('[role="alert"],small,[class*="hint"],[class*="help"]'));
 const selectionHeading=custom?Array.from(document.querySelectorAll('h1,h2,[role="heading"]')).find(node=>visible(node)&&/\b(select|choose)\b.*\b(account|patient)\b/i.test(text(node))):null;
 const confirmation=selectionHeading?Array.from(document.querySelectorAll('button')).find(node=>visible(node)&&!node.form&&/^confirm selection\b/i.test(text(node))):null;
 return {custom_clickable:custom,interaction_signals:custom?[(typeof el.onclick==='function'||el.hasAttribute('onclick'))?'dom_click_handler':'focusable_keyboard_handler']:[],selection_group:confirmation?text(selectionHeading).slice(0,180):null,selection_confirmation:confirmation?text(confirmation).slice(0,180):null,options:tag==='select'?Array.from(el.options).slice(0,100).filter(option=>!option.disabled&&!option.hidden&&!option.closest('optgroup')?.disabled).map(option=>({label:option.label.slice(0,200),selected:option.selected})):[],element:tag,role,input_type:el.type||type,has_form:!!el.form,in_popup:!!popup,popup_name:popup?scopeName(popup,popup.getAttribute('role')||'dialog'):null,focused:document.activeElement===el,name:squash(name).slice(0,300),placeholder:(el.getAttribute('placeholder')||'').slice(0,300),label:labelled.slice(0,300),
  nearby_text:helper.slice(0,180),scope,locator,disabled,readonly,required:!!el.required||el.getAttribute('aria-required')==='true',
  editable:!disabled&&!readonly&&(['input','textarea','select'].includes(tag)||el.isContentEditable),
  checked:el.matches('input[type="checkbox"],input[type="radio"]')?el.checked:el.getAttribute('aria-checked'),
  expanded:el.getAttribute('aria-expanded'),purpose,blocked_by_modal:blocked,actionable:!!locator&&!disabled&&!blocked,
  href:tag==='a'?el.href:null};
}
const identity=m=>JSON.stringify({locator:m.locator,scope:m.scope});
"""

EXTRACT = '(hint) => {' + HELPERS + r"""
 const nodes=candidateNodes(),records=[];
 for(const node of nodes.slice(0,3000)){const meta=inspect(node);if(meta)records.push({node,meta})}
 const counts=new Map();for(const item of records){if(!item.meta.actionable)continue;const key=identity(item.meta);counts.set(key,(counts.get(key)||0)+1)}
 const words=(hint||'').toLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
 for(const item of records){
  item.meta.ambiguous=counts.get(identity(item.meta))>1;
  if(item.meta.ambiguous)item.meta.actionable=false;
  const hay=JSON.stringify([item.meta.name,item.meta.placeholder,item.meta.scope,item.meta.nearby_text]).toLowerCase();
  item.rank=words.filter(word=>word.length>2&&hay.includes(word)).length*10+(item.meta.blocked_by_modal?-100:0);
 }
 records.sort((a,b)=>b.rank-a.rank);
 return {items:records.slice(0,120),total_controls:records.length,scan_truncated:nodes.length>3000,inventory_truncated:records.length>120};
}"""

VALIDATE = '(el,expected) => {' + HELPERS + r"""
 const meta=inspect(el);if(!meta||!meta.actionable||identity(meta)!==expected)return false;
 const nodes=candidateNodes();if(nodes.length>3000)return false;
 let matches=0;for(const node of nodes){const value=inspect(node);if(value&&value.actionable&&identity(value)===expected&&++matches>1)return false}
 return matches===1;
}"""


class ObservedControl:
    def __init__(self, node, signature):
        self.node = node
        self.signature = signature

    async def count(self):
        try:
            return int(await self.node.evaluate(VALIDATE, self.signature))
        except Exception:
            return 0

    async def is_visible(self):
        return await self.node.is_visible()

    async def guard(self):
        if not await self.count():
            raise StaleElementError("Observed control changed or became ambiguous before interaction. A new observation is required.")

    async def click(self):
        await self.guard()
        await self.node.click()

    async def fill(self, value):
        await self.guard()
        await self.node.fill(value)

    async def check(self):
        await self.guard()
        await self.node.check()

    async def uncheck(self):
        await self.guard()
        await self.node.uncheck()

    async def read_value(self):
        return await self.node.evaluate("el => 'value' in el ? el.value : el.textContent")

    async def read_options(self):
        return await self.node.evaluate("el => Array.from(el.selectedOptions||[]).map(option=>option.label)")

    async def press(self, value):
        await self.guard()
        await self.node.press(value)

    async def select_option(self, **kwargs):
        await self.guard()
        await self.node.select_option(**kwargs)

    async def wait_for(self, state="visible"):
        await self.guard()
        await self.node.wait_for_element_state(state)

    async def get_attribute(self, key):
        return await self.node.get_attribute(key)


def sanitize(value, redactor):
    if isinstance(value, str):
        return redactor.text(value, 500)
    if isinstance(value, list):
        return [sanitize(item, redactor) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, redactor) for key, item in value.items()}
    return value


async def discover(page, observation_id, redactor, hint=""):
    root = await page.evaluate_handle(EXTRACT, hint[:1000])
    items = await root.get_property("items")
    controls = []
    bindings = {}
    try:
        for item in (await items.get_properties()).values():
            meta_handle = await item.get_property("meta")
            raw = await meta_handle.json_value()
            await meta_handle.dispose()
            node_handle = await item.get_property("node")
            node = node_handle.as_element()
            signature = json.dumps({"locator": raw["locator"], "scope": raw["scope"]}, ensure_ascii=False, separators=(",", ":"))
            meta = sanitize(raw, redactor)
            if raw.get("href"):
                try:
                    meta["href"] = redactor.text(origin(raw["href"]) + urlsplit(raw["href"]).path, 1000)
                except ValueError:
                    meta.pop("href", None)
            identifier = f"{observation_id}-e{len(controls)}"
            meta["element_id"] = identifier
            controls.append(meta)
            if node and meta["actionable"]:
                bindings[identifier] = ObservedControl(node, signature)
            await item.dispose()
        metadata = {
            key: await (await root.get_property(key)).json_value()
            for key in ("total_controls", "scan_truncated", "inventory_truncated")
        }
        return controls, bindings, metadata
    finally:
        await items.dispose()
        await root.dispose()
