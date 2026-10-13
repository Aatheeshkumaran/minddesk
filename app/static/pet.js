/* MindDesk Pet — "Kiko / Pixel" robot cat
 * Drop-in, self-contained. Add ONE line to your index.html (before </body>):
 *   <script type="module" src="/static/pet.js"></script>
 * Use your own model:   src="/static/pet.js?model=/static/kiko.glb"
 *
 * Hooks for your chat code:
 *   MindDeskPet.say("text")          -> pet speaks in a bubble
 *   MindDeskPet.setThinking(true)    -> pet shows thinking face while Ollama answers
 *   MindDeskPet.setThinking(false)   -> pet cheers
 */
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/+esm";
import { RoundedBoxGeometry } from "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/geometries/RoundedBoxGeometry.js/+esm";

const params = new URL(import.meta.url).searchParams;
const MODEL_URL = params.get("model");
const W = 230, H = 290;

/* ---------- remove any old pet canvas/widget so they don't stack ---------- */
document.querySelectorAll("#pixel-pet, #pet, .pet-widget, #pet-canvas").forEach(n => n.remove());

/* ---------- DOM + CSS ---------- */
const css = document.createElement("style");
css.textContent = `
#mdpet{position:fixed;left:0;top:0;width:${W}px;height:${H}px;z-index:50;font-family:inherit;
  pointer-events:none;user-select:none;-webkit-user-select:none;touch-action:none;will-change:transform}
#mdpet .stage{position:absolute;inset:0;pointer-events:auto;cursor:grab}
#mdpet.dragging .stage,#mdpet.dragging .fab{cursor:grabbing}
#mdpet canvas{width:100%;height:100%;display:block;pointer-events:none}
#mdpet .bubble{position:absolute;left:50%;bottom:calc(100% + 2px);transform:translate(-50%,8px) scale(.92);
  max-width:210px;width:max-content;padding:8px 12px;border-radius:14px;font-size:12.5px;line-height:1.35;text-align:center;
  background:#fff;color:#14202e;box-shadow:0 6px 22px rgba(0,0,0,.35);opacity:0;pointer-events:none;
  transition:opacity .25s,transform .25s;z-index:1}
#mdpet .bubble.show{opacity:1;transform:translate(-50%,0) scale(1)}
#mdpet .bubble:after{content:"";position:absolute;left:50%;bottom:-6px;width:12px;height:12px;background:#fff;
  transform:translateX(-50%) rotate(45deg);border-radius:2px}
/* one icon holds every option; it is draggable and moves the pet with it */
#mdpet .fab{position:absolute;right:2px;bottom:2px;width:42px;height:42px;border-radius:50%;z-index:6;
  display:grid;place-items:center;font-size:20px;cursor:grab;pointer-events:auto;
  background:linear-gradient(145deg,#4dff7c,#1fb450);box-shadow:0 4px 14px rgba(0,0,0,.45),0 0 0 3px rgba(255,255,255,.12);
  transition:transform .15s}
#mdpet .fab:hover{transform:scale(1.08)}
#mdpet .menu{position:absolute;right:2px;bottom:52px;z-index:5;display:flex;flex-direction:column;gap:4px;padding:5px;
  border-radius:22px;background:rgba(24,26,34,.92);border:1px solid rgba(255,255,255,.12);backdrop-filter:blur(8px);
  opacity:0;transform:translateY(10px) scale(.9);transform-origin:bottom right;pointer-events:none;transition:opacity .18s,transform .18s}
#mdpet .menu.open{opacity:1;transform:none;pointer-events:auto}
#mdpet .menu button{all:unset;cursor:pointer;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;
  font-size:15px;background:rgba(255,255,255,.07);transition:transform .15s,background .15s}
#mdpet .menu button:hover{background:rgba(70,255,120,.28);transform:scale(1.1)}
#mdpet .menu button.off{opacity:.5}
#mdpet.min .stage{display:none}
`;
document.head.appendChild(css);

const root = document.createElement("div");
root.id = "mdpet";
root.innerHTML = `
  <div class="stage"></div>
  <div class="bubble"></div>
  <div class="menu">
    <button data-a="wave"  title="Wave">👋</button>
    <button data-a="pet"   title="Pet Kiko">💚</button>
    <button data-a="dance" title="Dance">🎵</button>
    <button data-a="talk"  title="Say something">💬</button>
    <button data-a="quiet" title="Mute self-chat">🔔</button>
    <button data-a="min"   title="Hide / show Kiko">🙈</button>
  </div>
  <div class="fab" title="Kiko menu (drag me!)">🐾</div>`;
document.body.appendChild(root);
const stage = root.querySelector(".stage"), fab = root.querySelector(".fab"), menu = root.querySelector(".menu");
const bubble = root.querySelector(".bubble");

/* ---------- renderer / scene ---------- */
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(W, H);
renderer.outputColorSpace = THREE.SRGBColorSpace;
stage.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, W / H, 0.1, 50);
function fit() {            // frame the WHOLE model so nothing can be cut off
  const box = new THREE.Box3().setFromObject(pet);
  box.max.y += 0.45; box.min.y -= 0.1;          // room for jumping + shadow
  const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
  const t = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
  const d = Math.max((size.y / 2) / t, (size.x * 1.15 / 2) / (t * camera.aspect)) * 1.08;
  camera.position.set(0, c.y, d); camera.lookAt(0, c.y, 0);
}

scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.25));
const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(3, 5, 5); scene.add(key);
const rim = new THREE.DirectionalLight(0x55ff88, 1.1); rim.position.set(-4, 2, -3); scene.add(rim);

/* ---------- materials ---------- */
const M = (c, r = 0.35, m = 0) => new THREE.MeshStandardMaterial({ color: c, roughness: r, metalness: m });
const white = M(0xf4f6f8, 0.3), navy = M(0x1b2a55, 0.55), green = M(0x39e060, 0.4), dark = M(0x0c0e12, 0.2, 0.2);
const rbox = (w, h, d, r, mat) => new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 5, r), mat);

/* ---------- pixel face (canvas texture) ---------- */
const FW = 32, FH = 20, PX = 12;
const fc = document.createElement("canvas"); fc.width = FW * PX; fc.height = FH * PX;
const fx = fc.getContext("2d");
const faceTex = new THREE.CanvasTexture(fc); faceTex.colorSpace = THREE.SRGBColorSpace;
const P = {
  arch: ["01110", "10001", "10001"],
  flat: ["11111"],
  up:   ["00100", "01010", "10001"],
  uMouth: ["10001", "01110"],
  grin: ["1000001", "0111110"],
  o:    ["0110", "1001", "1001", "0110"],
  dot:  ["11", "11"],
  zz:   ["1111", "0010", "0100", "1111"],
};
function blit(pat, x, y) {
  pat.forEach((row, j) => [...row].forEach((c, i) => c === "1" && fx.fillRect((x + i) * PX, (y + j) * PX, PX - 1, PX - 1)));
}
let faceName = "";
function setFace(name) {
  if (name === faceName) return; faceName = name;
  fx.fillStyle = "#0b0d11"; fx.fillRect(0, 0, fc.width, fc.height);
  fx.fillStyle = "#3dff6a"; fx.shadowColor = "#3dff6a"; fx.shadowBlur = 10;
  const eyes = { normal: P.arch, blink: P.flat, happy: P.up, surprised: P.o, sleepy: P.flat, think: P.dot }[name] || P.arch;
  const ey = name === "blink" || name === "sleepy" ? 7 : 5;
  blit(eyes, 6, ey); blit(eyes, 21, ey);
  if (name === "happy") blit(P.grin, 12, 12);
  else if (name === "surprised") blit(P.dot, 15, 13);
  else if (name === "think") { blit(P.dot, 12, 13); blit(P.dot, 15, 13); blit(P.dot, 18, 13); }
  else if (name === "sleepy") { blit(P.flat, 14, 13); blit(P.zz, 26, 1); }
  else blit(P.uMouth, 13, 12);
  faceTex.needsUpdate = true;
}
setFace("normal");

/* ---------- procedural Kiko ---------- */
const pet = new THREE.Group(); scene.add(pet);
const body = new THREE.Group(); pet.add(body);          // bobs
const headG = new THREE.Group(); headG.position.set(0, 1.7, 0); body.add(headG);

const head = rbox(1.7, 1.35, 1.25, 0.34, white); head.position.y = 0.62; headG.add(head);
const screen = rbox(1.42, 1.02, 0.16, 0.12, dark); screen.position.set(0, 0.62, 0.58); headG.add(screen);
const faceMat = new THREE.MeshBasicMaterial({ map: faceTex, transparent: true });
const face = new THREE.Mesh(new THREE.PlaneGeometry(1.32, 0.92), faceMat); face.position.set(0, 0.62, 0.67); headG.add(face);

function ear(sx) {
  const g = new THREE.Group();
  const o = new THREE.Mesh(new THREE.ConeGeometry(0.4, 0.78, 4), white); o.rotation.y = Math.PI / 4;
  const i = new THREE.Mesh(new THREE.ConeGeometry(0.26, 0.55, 4), green); i.rotation.y = Math.PI / 4; i.position.set(0, -0.04, 0.12);
  g.add(o, i); g.position.set(sx * 0.58, 1.5, 0.05); g.rotation.z = -sx * 0.28; return g;
}
const earL = ear(-1), earR = ear(1); headG.add(earL, earR);

function phone(sx) {
  const g = new THREE.Group();
  const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.2, 28), white); cup.rotation.z = Math.PI / 2;
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.035, 10, 28), green); ring.rotation.y = Math.PI / 2; ring.position.x = sx * 0.11;
  g.add(cup, ring); g.position.set(sx * 0.9, 0.62, 0); return g;
}
headG.add(phone(-1), phone(1));

// neck
const neck = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.26, 0.3, 20), dark); neck.position.y = 1.65; body.add(neck);
// torso
const torso = rbox(1.1, 1.0, 0.7, 0.2, navy); torso.position.y = 1.1; body.add(torso);
const shirt = rbox(0.34, 0.62, 0.05, 0.02, white); shirt.position.set(0, 1.2, 0.36); body.add(shirt);
const knot = rbox(0.12, 0.1, 0.06, 0.02, green); knot.position.set(0, 1.45, 0.4); body.add(knot);
const tie = new THREE.Mesh(new THREE.ConeGeometry(0.1, 0.42, 3), green); tie.rotation.set(Math.PI, 0, 0); tie.position.set(0, 1.2, 0.4); tie.scale.z = 0.4; body.add(tie);
[-1, 1].forEach(s => { const l = rbox(0.06, 0.62, 0.05, 0.02, green); l.position.set(s * 0.22, 1.17, 0.37); l.rotation.z = s * 0.18; body.add(l); });

function arm(sx) {
  const sh = new THREE.Group(); sh.position.set(sx * 0.66, 1.42, 0);
  const up = new THREE.Mesh(new THREE.CapsuleGeometry(0.16, 0.5, 6, 14), navy); up.position.y = -0.3;
  const cuff = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.03, 8, 20), green); cuff.rotation.x = Math.PI / 2; cuff.position.y = -0.52;
  const hand = new THREE.Mesh(new THREE.SphereGeometry(0.2, 18, 14), white); hand.position.y = -0.7;
  sh.add(up, cuff, hand); sh.rotation.z = sx * 0.18; body.add(sh); return sh;
}
const armL = arm(-1), armR = arm(1);

function leg(sx) {
  const g = new THREE.Group(); g.position.set(sx * 0.3, 0.66, 0);
  const l = new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.3, 6, 14), white); l.position.y = -0.1;
  const stripe = new THREE.Mesh(new THREE.TorusGeometry(0.175, 0.025, 8, 20), green); stripe.rotation.x = Math.PI / 2; stripe.position.y = -0.2;
  const foot = rbox(0.4, 0.2, 0.55, 0.09, white); foot.position.set(0, -0.42, 0.08);
  const fs = rbox(0.42, 0.04, 0.57, 0.02, green); fs.position.set(0, -0.5, 0.08);
  g.add(l, stripe, foot, fs); body.add(g); return g;
}
const legL = leg(-1), legR = leg(1);

// soft shadow
const sc = document.createElement("canvas"); sc.width = sc.height = 64;
const sg = sc.getContext("2d"), gr = sg.createRadialGradient(32, 32, 2, 32, 32, 30);
gr.addColorStop(0, "rgba(0,0,0,.45)"); gr.addColorStop(1, "rgba(0,0,0,0)"); sg.fillStyle = gr; sg.fillRect(0, 0, 64, 64);
const shadow = new THREE.Mesh(new THREE.PlaneGeometry(2.2, 2.2), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sc), transparent: true, depthWrite: false }));
shadow.rotation.x = -Math.PI / 2; shadow.position.y = 0.02; scene.add(shadow);
pet.position.y = 0.5; shadow.position.y = 0.52;
fit();

/* ---------- optional custom model (.glb) ---------- */
let custom = null, mixer = null;
if (MODEL_URL) {
  import("https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/GLTFLoader.js/+esm").then(({ GLTFLoader }) => {
    new GLTFLoader().load(MODEL_URL, gltf => {
      custom = gltf.scene;
      const box = new THREE.Box3().setFromObject(custom), size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
      const s = 3.3 / size.y; custom.scale.setScalar(s);
      custom.position.set(-c.x * s, -box.min.y * s, -c.z * s);
      body.visible = false; pet.add(custom); fit();
      if (gltf.animations.length) { mixer = new THREE.AnimationMixer(custom); mixer.clipAction(gltf.animations[0]).play(); }
    }, undefined, err => console.warn("MindDeskPet: model failed, using built-in Kiko", err));
  }).catch(() => {});
}

/* ---------- behaviour ---------- */
const clock = new THREE.Clock();
let state = "idle", stateT = 0, thinking = false, quiet = false;
let mouse = { x: 0, y: 0 }, blinkIn = 3, nextChat = 14, bubbleTimer = 0;
addEventListener("pointermove", e => { mouse.x = (e.clientX / innerWidth) * 2 - 1; mouse.y = (e.clientY / innerHeight) * 2 - 1; });

const idleLines = [
  "Need a hand thinking this through? 🤔", "Tip: Shift+Enter makes a new line.", "I'm Kiko, your desk cat!",
  "Stuck? Try 'Brainstorm ideas'.", "Stretch break? Your shoulders will thank you.", "n_n  I'm watching your Ollama logs…",
  "Drink some water! 💧", "Big goals = small steps.",
];
const pokeLines = ["Hehe, that tickles!", "Purr… I mean, beep!", "Again again! 💚", "Meow.exe running ✔"];
const rnd = a => a[Math.floor(Math.random() * a.length)];

function say(text, ms = 3800) {
  bubble.textContent = text; bubble.classList.add("show");
  clearTimeout(bubbleTimer); bubbleTimer = setTimeout(() => bubble.classList.remove("show"), ms);
}
function act(name, dur, f) { state = name; stateT = 0; act.dur = dur; if (f) setFace(f); }
act.dur = 0;

function setThinking(v) {
  thinking = !!v;
  if (v) { setFace("think"); say("Hmm, thinking…", 2500); }
  else { act("happy", 1.8, "happy"); say("Done! Hope that helps ✨", 2500); }
}

menu.addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  const a = b.dataset.a;
  if (a === "wave") { act("wave", 2.2, "happy"); say("Hi there! 👋"); }
  if (a === "pet") { act("happy", 2.2, "happy"); say(rnd(pokeLines)); }
  if (a === "dance") { act("dance", 4, "happy"); say("🎵 beep boop 🎵"); }
  if (a === "talk") { act("happy", 1.5, "happy"); say(rnd(idleLines)); nextChat = 25; }
  if (a === "quiet") { quiet = !quiet; b.classList.toggle("off", quiet); b.textContent = quiet ? "🔕" : "🔔"; say(quiet ? "Okay, I'll stay quiet." : "Chatty mode on!"); return; }
  if (a === "min") { root.classList.toggle("min"); b.textContent = root.classList.contains("min") ? "🐱" : "🙈"; }
  menu.classList.remove("open");
});

/* ---------- dragging (pet AND icon move together) ---------- */
let pos = { x: innerWidth - W - 16, y: innerHeight - H - 24 }, drag = null, dragging = false;
try { const sv = JSON.parse(localStorage.getItem("mdpet.pos")); if (sv && isFinite(sv.x) && isFinite(sv.y)) pos = sv; } catch (e) {}
function place() {
  pos.x = Math.min(Math.max(pos.x, -40), innerWidth - W + 40);
  pos.y = Math.min(Math.max(pos.y, 60), innerHeight - 60);       // keep bubble on-screen, keep icon reachable
  root.style.transform = `translate(${pos.x}px,${pos.y}px)`;
}
place(); addEventListener("resize", place);
function down(e) { drag = { sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false, el: e.currentTarget }; e.currentTarget.setPointerCapture(e.pointerId); }
function move(e) {
  if (!drag) return;
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (!drag.moved && Math.hypot(dx, dy) < 5) return;
  if (!drag.moved) { drag.moved = true; dragging = true; root.classList.add("dragging"); menu.classList.remove("open"); setFace("surprised"); }
  pos.x = drag.ox + dx; pos.y = drag.oy + dy; place();
}
function up() {
  if (!drag) return;
  const d = drag; drag = null; dragging = false; root.classList.remove("dragging");
  if (d.moved) { try { localStorage.setItem("mdpet.pos", JSON.stringify(pos)); } catch (e) {} act("happy", 1.4, "happy"); say("Whee! 🚀", 1800); }
  else if (d.el === fab) menu.classList.toggle("open");
  else { act("happy", 1.8, "surprised"); setTimeout(() => setFace("happy"), 250); say(rnd(pokeLines)); }
}
[stage, fab].forEach(el => { el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move); el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up); });

function tick() {
  requestAnimationFrame(tick);
  const dt = Math.min(clock.getDelta(), 0.05), t = clock.elapsedTime;
  stateT += dt; mixer && mixer.update(dt);

  if (!dragging && state !== "idle" && stateT > act.dur) { state = "idle"; setFace(thinking ? "think" : "normal"); }

  // base idle: breathing + head follows cursor
  body.position.y = Math.sin(t * 2) * 0.04;
  headG.rotation.y += (mouse.x * 0.5 - headG.rotation.y) * 0.08;
  headG.rotation.x += (mouse.y * 0.12 - headG.rotation.x) * 0.08;
  headG.rotation.z = Math.sin(t * 1.3) * 0.03;
  earL.rotation.z = 0.28 + Math.sin(t * 3 + 1) * 0.03; earR.rotation.z = -0.28 - Math.sin(t * 3) * 0.03;
  armL.rotation.z = -0.18 + Math.sin(t * 2) * 0.03; armR.rotation.z = 0.18 - Math.sin(t * 2) * 0.03;
  armL.rotation.x = armR.rotation.x = 0; pet.rotation.y = Math.sin(t * 0.6) * 0.08;
  legL.rotation.x = legR.rotation.x = 0; pet.position.y = 0.5;

  if (state === "wave") { armR.rotation.z = 2.5 + Math.sin(stateT * 12) * 0.35; }
  if (state === "dance") {
    pet.position.y = 0.5 + Math.abs(Math.sin(stateT * 7)) * 0.3;
    pet.rotation.y = Math.sin(stateT * 3.5) * 0.5;
    armL.rotation.z = -2 + Math.sin(stateT * 7) * 0.4; armR.rotation.z = 2 - Math.sin(stateT * 7) * 0.4;
    headG.rotation.z = Math.sin(stateT * 7) * 0.14;
  }
  if (state === "happy") { pet.position.y = 0.5 + Math.abs(Math.sin(stateT * 9)) * 0.18 * Math.max(0, 1 - stateT / act.dur); }
  if (thinking && state === "idle") { headG.rotation.z = 0.12; armR.rotation.z = 1.2; armR.rotation.x = -0.9; }

  if (dragging) {                      // dangling pose while being carried
    armL.rotation.z = -2.3 + Math.sin(t * 14) * 0.2; armR.rotation.z = 2.3 - Math.sin(t * 14) * 0.2;
    legL.rotation.x = Math.sin(t * 12) * 0.5; legR.rotation.x = -Math.sin(t * 12) * 0.5;
    pet.position.y = 0.75; headG.rotation.z = Math.sin(t * 10) * 0.1;
  }

  // blink
  blinkIn -= dt;
  if (blinkIn < 0 && state === "idle" && !thinking && !dragging) { setFace("blink"); setTimeout(() => state === "idle" && !thinking && setFace("normal"), 130); blinkIn = 2.5 + Math.random() * 3.5; }

  // occasional self-chat (rare, short, and mutable)
  nextChat -= dt;
  if (nextChat < 0) { if (!quiet && !thinking && !dragging && !root.classList.contains("min")) { act("happy", 1.2, "happy"); say(rnd(idleLines), 3200); } nextChat = 35 + Math.random() * 35; }

  shadow.scale.setScalar(1 - (pet.position.y - 0.5) * 0.6);
  renderer.render(scene, camera);
}
tick();

window.MindDeskPet = { say, setThinking, wave: () => act("wave", 2.2, "happy"), dance: () => act("dance", 4, "happy") };
setTimeout(() => { act("wave", 2.2, "happy"); say("Hi! I'm Kiko 🐱"); }, 800);

console.log("MindDeskPet v3 loaded (draggable, auto-fit camera)");