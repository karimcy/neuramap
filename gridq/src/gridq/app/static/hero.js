/* GridQ cinematic hero - the sizing surface (Power x Duration x Return) as live WebGL.
 *
 * Contract (cinematic-redesign spec + the house invariants):
 * - REAL data only: the mesh comes from the `hero-surface-data` island (the newest solved
 *   run's persisted sizing cloud via view.surface_island). No island = no invented surface:
 *   the static SVG fallback stays in place. The module positions and draws; it never
 *   computes or invents a figure.
 * - Progressive enhancement: the fallback SVG lives in the DOM; the canvas replaces it only
 *   after a successful WebGL init. WebGL unavailable / context lost = fallback remains.
 * - Performance: lazy-init on first intersection, pixel ratio capped at 2, rendering pauses
 *   off-screen and on document.hidden; the render loop is the ONLY perpetual animation
 *   besides status dots (motion discipline).
 * - Reduced motion: a single static frame - no camera drift, no beacon pulse.
 *
 * Loaded as an ES module (deferred, after FCP); Three.js is vendored at
 * /static/vendor/three/three.module.min.js (no CDN) and DYNAMICALLY imported only when
 * the hero actually initialises, after window load + an idle slot - its parse cost never
 * sits in the interactive window (performance budget: TBT).
 */

let THREE = null;

async function loadThree() {
  if (!THREE) THREE = await import("./vendor/three/three.module.min.js");
  return THREE;
}

const MOUNT_ID = "hero-3d";
const DATA_ID = "hero-surface-data";

function readIsland() {
  const el = document.getElementById(DATA_ID);
  if (!el) return null;
  try {
    const s = JSON.parse(el.textContent);
    if (!s || !s.dense || !s.has_surface) return null;
    return s;
  } catch {
    return null;
  }
}

function accent() {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  return v || "#ffb454";
}

/* Amber height ramp (display geometry): charcoal floor to the accent peak. */
const RAMP = [
  [0.0, 0x131316],
  [0.35, 0x3a2f1d],
  [0.65, 0x8a6630],
  [0.85, 0xc08a3e],
  [1.0, 0xffb454],
];

function rampColor(t) {
  for (let i = 1; i < RAMP.length; i++) {
    if (t <= RAMP[i][0]) {
      const [t0, c0] = RAMP[i - 1];
      const [t1, c1] = RAMP[i];
      const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
      return new THREE.Color(c0).lerp(new THREE.Color(c1), f);
    }
  }
  return new THREE.Color(RAMP[RAMP.length - 1][1]);
}

function buildSurface(island) {
  const zs = island.dense_z;
  const rows = zs.length;
  const cols = zs[0].length;
  let zMin = Infinity;
  let zMax = -Infinity;
  for (const row of zs) {
    for (const v of row) {
      if (v === null) continue;
      if (v < zMin) zMin = v;
      if (v > zMax) zMax = v;
    }
  }
  const span = zMax - zMin || 1;

  const geo = new THREE.PlaneGeometry(3.2, 2.4, cols - 1, rows - 1);
  const pos = geo.attributes.position;
  const colors = new Float32Array(pos.count * 3);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c;
      const v = zs[r][c];
      const t = v === null ? 0 : (v - zMin) / span;
      pos.setZ(i, t * 0.9);
      const col = rampColor(t);
      colors[i * 3] = col.r;
      colors[i * 3 + 1] = col.g;
      colors[i * 3 + 2] = col.b;
    }
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();

  const mat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    metalness: 0.15,
    roughness: 0.82,
    flatShading: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2.35;

  const wire = new THREE.LineSegments(
    new THREE.WireframeGeometry(geo),
    new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.05 })
  );
  wire.rotation.copy(mesh.rotation);

  // Optimum beacon at the selected point (real record coordinates mapped onto the mesh).
  let beacon = null;
  const sel = island.selected;
  if (sel && sel.z !== null && sel.z !== undefined) {
    const px = island.dense_powers;
    const py = island.dense_durations;
    const fx = (sel.power_kw - px[0]) / (px[px.length - 1] - px[0] || 1);
    const fy = (sel.duration_hours - py[0]) / (py[py.length - 1] - py[0] || 1);
    const zt = (sel.z - zMin) / span;
    const local = new THREE.Vector3((fx - 0.5) * 3.2, (0.5 - fy) * 2.4, zt * 0.9 + 0.06);
    const world = local.clone().applyEuler(mesh.rotation);
    beacon = new THREE.Group();
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(0.035, 16, 16),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(accent()) })
    );
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.055, 0.07, 32),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(accent()),
        transparent: true,
        opacity: 0.8,
        side: THREE.DoubleSide,
      })
    );
    ring.rotation.x = -Math.PI / 2.35;
    beacon.add(core);
    beacon.add(ring);
    beacon.position.copy(world);
    beacon.userData.ring = ring;
  }

  return { mesh, wire, beacon };
}

async function init() {
  const mount = document.getElementById(MOUNT_ID);
  const island = readIsland();
  if (!mount || !island) return;
  try {
    await loadThree();
  } catch {
    return; // module unavailable: the SVG fallback stays
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch {
    return; // WebGL unavailable: the SVG fallback stays.
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 50);
  camera.position.set(0, 1.55, 3.3);
  camera.lookAt(0, 0.18, 0);

  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.position.set(2.4, 3.2, 2.0);
  scene.add(key);
  scene.add(new THREE.AmbientLight(0xffffff, 0.42));
  const rim = new THREE.DirectionalLight(0x8890ff, 0.5);
  rim.position.set(-2.5, 1.2, -2.0);
  scene.add(rim);

  const { mesh, wire, beacon } = buildSurface(island);
  scene.add(mesh);
  scene.add(wire);
  if (beacon) scene.add(beacon);

  function size() {
    const w = mount.clientWidth || 640;
    const h = mount.clientHeight || 420;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  size();
  window.addEventListener("resize", size);

  // Swap the fallback for the live canvas only now that init succeeded.
  renderer.domElement.setAttribute("aria-hidden", "true");
  mount.replaceChildren(renderer.domElement);
  mount.classList.add("hero-3d-live");

  let running = false;
  let rafId = 0;
  let t = 0;

  function frame() {
    if (!running) return;
    t += 0.0022; // slow autonomous drift
    camera.position.x = Math.sin(t) * 0.55;
    camera.position.z = 3.3 + Math.cos(t * 0.8) * 0.18;
    camera.lookAt(0, 0.18, 0);
    if (beacon) {
      const pulse = 0.75 + 0.25 * Math.sin(t * 26);
      beacon.userData.ring.material.opacity = 0.35 + 0.45 * pulse;
      beacon.userData.ring.scale.setScalar(1 + 0.35 * (1 - pulse));
    }
    renderer.render(scene, camera);
    rafId = requestAnimationFrame(frame);
  }

  function start() {
    if (running || reduced) return;
    running = true;
    rafId = requestAnimationFrame(frame);
  }
  function stop() {
    running = false;
    cancelAnimationFrame(rafId);
  }

  if (reduced) {
    renderer.render(scene, camera); // one static frame
  } else {
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => (e.isIntersecting ? start() : stop())),
      { threshold: 0.05 }
    );
    io.observe(mount);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (mount.getBoundingClientRect().bottom > 0) start();
    });
  }

  renderer.domElement.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    stop();
  });
}

/* Lazy: arm only after window load + an idle slot (keeps Three's parse out of the
 * interactive window), then initialise when the hero approaches the viewport. */
function whenIdle(fn) {
  const go = () =>
    "requestIdleCallback" in window ? requestIdleCallback(fn, { timeout: 3000 }) : setTimeout(fn, 250);
  if (document.readyState === "complete") go();
  else window.addEventListener("load", go, { once: true });
}

/* Hardware-accelerated WebGL only: with a software rasteriser (SwiftShader/llvmpipe) the
 * probe context below is refused and the static SVG stays - no module parse, no shader
 * compile, no CPU-melting loop. The standard progressive-enhancement check. */
function hardwareGL() {
  try {
    const probe = document.createElement("canvas");
    const gl =
      probe.getContext("webgl2", { failIfMajorPerformanceCaveat: true }) ||
      probe.getContext("webgl", { failIfMajorPerformanceCaveat: true });
    if (!gl) return false;
    // Newer Chrome ships SwiftShader-Vulkan as "conformant", so the caveat flag alone no
    // longer catches software rasterisers - check the unmasked renderer name as well.
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    const rname = dbg ? String(gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL)) : "";
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return !/swiftshader|llvmpipe|softpipe|software/i.test(rname);
  } catch {
    return false;
  }
}

/* Wide viewports only: on phones the hero art sits below the fold and the static SVG is
 * the better trade (no 690KB module parse, no WebGL on battery). The breakpoint matches
 * the hero grid collapse in app.css. */
const mountEl = document.getElementById(MOUNT_ID);
if (mountEl && window.matchMedia("(min-width: 880px)").matches && hardwareGL()) {
  whenIdle(() => {
    if ("IntersectionObserver" in window) {
      const lazy = new IntersectionObserver(
        (entries, obs) => {
          entries.forEach((e) => {
            if (e.isIntersecting) {
              obs.disconnect();
              init();
            }
          });
        },
        { rootMargin: "200px" }
      );
      lazy.observe(mountEl);
    } else {
      init();
    }
  });
}
