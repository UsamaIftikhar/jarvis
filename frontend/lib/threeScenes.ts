/**
 * Three.js scene controller for the JARVIS HUD.
 *
 * Layers (all live in scene simultaneously, cross-faded by opacity):
 *   - centerOrb    : glowing core sphere that morphs per state
 *   - rings        : 3 counter-rotating arcs + compass ticks + radar sweep (IDLE)
 *   - listenRing   : 96 radial FFT bars + inner pulse ring (LISTENING)
 *   - particles    : ~1800 sphere points + 2 orbital rings (THINKING)
 *   - speakCircle  : 128 radial waveform bars around core + outer ring pulse (SPEAKING)
 */
import * as THREE from "three";
import type { JarvisState } from "./state";
import { audioBus } from "@/lib/audioBus";

const COLOR_BLUE   = new THREE.Color(0x00d4ff);
const COLOR_CYAN   = new THREE.Color(0x00ffff);
const COLOR_WHITE  = new THREE.Color(0xffffff);
const COLOR_ORANGE = new THREE.Color(0xff7700);

const LISTEN_BARS = 96;
const SPEAK_BARS  = 128;
const PARTICLE_COUNT = 1800;

export interface SceneController {
  setState: (next: JarvisState) => void;
  resize: (width: number, height: number) => void;
  dispose: () => void;
}

interface LayerOpacities {
  rings: number;
  listen: number;
  think: number;
  speak: number;
}

export function createScene(canvas: HTMLCanvasElement): SceneController {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 6;

  // Build layers
  const centerOrb   = makeCenterOrb();
  const rings       = makeRings();
  const listenRing  = makeListenRing();
  const particles   = makeParticles();
  const speakCircle = makeSpeakCircle();

  scene.add(centerOrb.group);
  scene.add(rings.group);
  scene.add(listenRing.group);
  scene.add(particles.group);
  scene.add(speakCircle.group);

  const micFreq  = new Uint8Array(256);
  const playFreq = new Uint8Array(256);
  const playWave = new Uint8Array(1024);

  let currentState: JarvisState = "IDLE";
  const opacities: LayerOpacities = { rings: 1, listen: 0, think: 0, speak: 0 };

  let raf = 0;
  const clock = new THREE.Clock();

  const animate = () => {
    raf = requestAnimationFrame(animate);
    const now = clock.getElapsedTime();
    clock.getDelta(); // advance delta (consumed in lerp below)
    const rawDt = 1 / 60; // approximate; avoids getDelta double-call
    const dt = Math.min(0.05, rawDt);

    const target = targetOpacities(currentState);
    const k = 1 - Math.exp(-dt * 6.0);
    opacities.rings  = lerp(opacities.rings,  target.rings,  k);
    opacities.listen = lerp(opacities.listen, target.listen, k);
    opacities.think  = lerp(opacities.think,  target.think,  k);
    opacities.speak  = lerp(opacities.speak,  target.speak,  k);

    // Pull audio data once
    let micRms = 0;
    if (opacities.listen > 0.01) {
      const an = audioBus.getMicAnalyser();
      if (an) {
        an.getByteFrequencyData(micFreq);
        let sum = 0;
        for (let i = 0; i < micFreq.length; i++) sum += micFreq[i]!;
        micRms = sum / micFreq.length / 255;
      } else {
        for (let i = 0; i < micFreq.length; i++)
          micFreq[i] = Math.floor(80 + 30 * Math.sin(now * 2.0 + i * 0.25));
      }
    }

    let speakRms = 0;
    if (opacities.speak > 0.01) {
      const an = audioBus.getPlaybackAnalyser();
      if (an) {
        an.getByteFrequencyData(playFreq);
        an.getByteTimeDomainData(playWave);
        let sum = 0;
        for (let i = 0; i < playFreq.length; i++) sum += playFreq[i]!;
        speakRms = sum / playFreq.length / 255;
      } else {
        for (let i = 0; i < playWave.length; i++)
          playWave[i] = 128 + Math.floor(20 * Math.sin(now * 5.0 + i * 0.05));
      }
    }

    const thinkRms = opacities.think > 0.01 ? 0.5 + 0.5 * Math.sin(now * 2.3) : 0;

    // Update layers
    centerOrb.update(now, currentState, opacities, micRms, speakRms, thinkRms);
    rings.update(now, opacities.rings, micRms);

    if (opacities.listen > 0.01) listenRing.update(now, opacities.listen, micFreq, micRms);
    else listenRing.setVisible(false);

    if (opacities.think > 0.01) particles.update(now, opacities.think);
    else particles.setVisible(false);

    if (opacities.speak > 0.01) speakCircle.update(now, opacities.speak, playWave, speakRms);
    else speakCircle.setVisible(false);

    renderer.render(scene, camera);
  };
  animate();

  return {
    setState(next) { currentState = next; },
    resize(width, height) {
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
    },
    dispose() {
      cancelAnimationFrame(raf);
      centerOrb.dispose();
      rings.dispose();
      listenRing.dispose();
      particles.dispose();
      speakCircle.dispose();
      renderer.dispose();
    },
  };
}

// ====================================================================
// Layer factories
// ====================================================================

interface Layer {
  setVisible(v: boolean): void;
  dispose(): void;
}

// --- CENTER ORB ---------------------------------------------------

interface OrbLayer extends Layer {
  group: THREE.Group;
  update(
    t: number,
    state: JarvisState,
    op: LayerOpacities,
    micRms: number,
    speakRms: number,
    thinkRms: number,
  ): void;
}

function makeCenterOrb(): OrbLayer {
  const group = new THREE.Group();

  // Inner bright core
  const coreGeo = new THREE.SphereGeometry(0.14, 32, 32);
  const coreMat = new THREE.MeshBasicMaterial({
    color: COLOR_CYAN.clone(),
    transparent: true,
    opacity: 0.95,
  });
  const core = new THREE.Mesh(coreGeo, coreMat);

  // Mid glow shell
  const midGeo = new THREE.SphereGeometry(0.28, 32, 32);
  const midMat = new THREE.MeshBasicMaterial({
    color: COLOR_BLUE.clone(),
    transparent: true,
    opacity: 0.25,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.BackSide,
  });
  const mid = new THREE.Mesh(midGeo, midMat);

  // Outer diffuse halo
  const outerGeo = new THREE.SphereGeometry(0.52, 32, 32);
  const outerMat = new THREE.MeshBasicMaterial({
    color: COLOR_BLUE.clone(),
    transparent: true,
    opacity: 0.08,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.BackSide,
  });
  const outer = new THREE.Mesh(outerGeo, outerMat);

  // Ring around the orb
  const ringGeo = new THREE.RingGeometry(0.18, 0.19, 128);
  const ringMat = new THREE.MeshBasicMaterial({
    color: COLOR_CYAN.clone(),
    transparent: true,
    opacity: 0.7,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const equatorRing = new THREE.Mesh(ringGeo, ringMat);

  group.add(outer, mid, core, equatorRing);

  return {
    group,
    setVisible(v) { group.visible = v; },
    update(t, state, op, micRms, speakRms, thinkRms) {
      group.visible = true;

      // Choose target color and scale per state
      let targetColor: THREE.Color;
      let coreScale: number;
      let midOpacity: number;
      let outerOpacity: number;

      switch (state) {
        case "IDLE":
          targetColor = COLOR_CYAN;
          coreScale   = 1.0 + Math.sin(t * 1.1) * 0.06;
          midOpacity  = 0.18 + Math.sin(t * 0.9) * 0.05;
          outerOpacity = 0.06;
          break;
        case "LISTENING":
          targetColor = COLOR_CYAN;
          coreScale   = 1.1 + micRms * 0.6 + Math.sin(t * 3.0) * 0.04;
          midOpacity  = 0.3 + micRms * 0.3;
          outerOpacity = 0.1 + micRms * 0.15;
          break;
        case "THINKING":
          targetColor = COLOR_WHITE;
          coreScale   = 0.9 + thinkRms * 0.3;
          midOpacity  = 0.35 + thinkRms * 0.15;
          outerOpacity = 0.12 + thinkRms * 0.06;
          break;
        case "SPEAKING":
          targetColor = COLOR_BLUE;
          coreScale   = 1.0 + speakRms * 0.8 + Math.sin(t * 8.0) * 0.04 * speakRms;
          midOpacity  = 0.4 + speakRms * 0.35;
          outerOpacity = 0.12 + speakRms * 0.18;
          break;
        case "ERROR":
          targetColor = COLOR_ORANGE;
          coreScale   = 1.0 + Math.sin(t * 8.0) * 0.08;
          midOpacity  = 0.25;
          outerOpacity = 0.08;
          break;
      }

      coreMat.color.lerp(targetColor, 0.1);
      midMat.color.lerp(targetColor, 0.06);
      outerMat.color.lerp(targetColor, 0.04);
      ringMat.color.lerp(targetColor, 0.08);

      const totalOp = Math.max(op.rings, op.listen, op.think, op.speak);
      core.scale.setScalar(coreScale);
      coreMat.opacity = 0.95 * Math.min(1, totalOp * 2);
      midMat.opacity  = midOpacity  * Math.min(1, totalOp * 2);
      outerMat.opacity = outerOpacity * Math.min(1, totalOp * 2);

      equatorRing.rotation.x = t * 0.7;
      equatorRing.rotation.y = t * 0.4;
      ringMat.opacity = (0.5 + (speakRms + micRms) * 0.5) * Math.min(1, totalOp * 2);
    },
    dispose() {
      [coreGeo, midGeo, outerGeo, ringGeo].forEach(g => g.dispose());
      [coreMat, midMat, outerMat, ringMat].forEach(m => m.dispose());
    },
  };
}

// --- IDLE RINGS + RADAR SWEEP ------------------------------------

interface RingsLayer extends Layer {
  group: THREE.Group;
  update(t: number, opacity: number, micRms: number): void;
}

function makeRings(): RingsLayer {
  const group = new THREE.Group();

  const outerArc = makeArc(2.10, 0.008, COLOR_BLUE,  0.9);
  const midArc   = makeArc(1.70, 0.006, COLOR_CYAN,  0.5);
  const innerArc = makeArc(1.35, 0.005, COLOR_BLUE,  0.35);
  group.add(outerArc, midArc, innerArc);

  // Compass ticks on the outer ring
  const ticks = new THREE.Group();
  for (let i = 0; i < 36; i++) {
    const angle  = (i / 36) * Math.PI * 2;
    const major  = i % 3 === 0;
    const len    = major ? 0.10 : 0.05;
    const geo    = new THREE.PlaneGeometry(0.004, len);
    const mat    = new THREE.MeshBasicMaterial({
      color: COLOR_BLUE,
      transparent: true,
      opacity: major ? 0.8 : 0.35,
    });
    mat.userData.base = mat.opacity;
    const tick = new THREE.Mesh(geo, mat);
    const r = 2.24;
    tick.position.set(Math.cos(angle) * r, Math.sin(angle) * r, 0);
    tick.rotation.z = angle - Math.PI / 2;
    ticks.add(tick);
  }
  group.add(ticks);

  // Radar sweep — a thin sector that rotates
  const sweepPoints: THREE.Vector2[] = [];
  sweepPoints.push(new THREE.Vector2(0, 0));
  const sweepAngle = Math.PI / 18; // 10 degrees
  const sweepR = 2.12;
  const segs = 12;
  for (let i = 0; i <= segs; i++) {
    const a = (i / segs) * sweepAngle;
    sweepPoints.push(new THREE.Vector2(Math.cos(a) * sweepR, Math.sin(a) * sweepR));
  }
  const sweepShape = new THREE.Shape(sweepPoints);
  const sweepGeo   = new THREE.ShapeGeometry(sweepShape);
  const sweepMat   = new THREE.MeshBasicMaterial({
    color: COLOR_CYAN,
    transparent: true,
    opacity: 0.22,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const sweep = new THREE.Mesh(sweepGeo, sweepMat);
  group.add(sweep);

  // Dashed data arc (second outer decoration, partial arc segments)
  const dashGroup = new THREE.Group();
  const dashCount = 16;
  for (let i = 0; i < dashCount; i++) {
    const angle = (i / dashCount) * Math.PI * 2;
    if (i % 3 === 1) continue; // skip every 3rd = dashed effect
    const dGeo = new THREE.RingGeometry(2.32, 2.33, 1, 1, angle, (1 / dashCount) * Math.PI * 2 * 0.7);
    const dMat = new THREE.MeshBasicMaterial({
      color: COLOR_BLUE,
      transparent: true,
      opacity: 0.3,
      side: THREE.DoubleSide,
    });
    dashGroup.add(new THREE.Mesh(dGeo, dMat));
  }
  group.add(dashGroup);

  return {
    group,
    setVisible(v) { group.visible = v; },
    update(t, opacity, micRms) {
      group.visible = opacity > 0.01;

      outerArc.rotation.z = t * 0.12;
      midArc.rotation.z   = -t * 0.18;
      innerArc.rotation.z = t * 0.28;
      ticks.rotation.z    = t * 0.04;
      dashGroup.rotation.z = -t * 0.03;

      // Radar sweep rotates continuously
      sweep.rotation.z = -t * 0.8;

      const breathe = 1 + Math.sin(t * 1.1) * 0.02;
      group.scale.setScalar(breathe);

      const micBoost = 1 + micRms * 0.3;
      setOpacity(outerArc, 0.9 * opacity * micBoost);
      setOpacity(midArc,   0.5 * opacity);
      setOpacity(innerArc, 0.35 * opacity);
      sweepMat.opacity = 0.22 * opacity;
    },
    dispose() {
      disposeMesh(outerArc); disposeMesh(midArc); disposeMesh(innerArc);
      ticks.children.forEach(c => disposeMesh(c as THREE.Mesh));
      disposeMesh(sweep);
      dashGroup.children.forEach(c => disposeMesh(c as THREE.Mesh));
    },
  };
}

// --- LISTENING: circular FFT bars + inner pulse ring --------------

interface ListenLayer extends Layer {
  group: THREE.Group;
  update(t: number, opacity: number, freq: Uint8Array, rms: number): void;
}

function makeListenRing(): ListenLayer {
  const group = new THREE.Group();
  const radius = 1.85;
  const bars: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>[] = [];

  for (let i = 0; i < LISTEN_BARS; i++) {
    const geo = new THREE.PlaneGeometry(0.026, 0.05);
    const mat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN.clone(),
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const bar = new THREE.Mesh(geo, mat);
    const angle = (i / LISTEN_BARS) * Math.PI * 2;
    bar.position.set(Math.cos(angle) * radius, Math.sin(angle) * radius, 0);
    bar.rotation.z = angle - Math.PI / 2;
    bar.userData.angle = angle;
    bars.push(bar);
    group.add(bar);
  }

  // Inner pulse ring that breathes with RMS
  const pulseRingGeo = new THREE.RingGeometry(0.9, 0.905, 128);
  const pulseRingMat = new THREE.MeshBasicMaterial({
    color: COLOR_CYAN,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const pulseRing = new THREE.Mesh(pulseRingGeo, pulseRingMat);
  group.add(pulseRing);

  // Secondary larger pulse ring
  const pulse2Geo = new THREE.RingGeometry(1.4, 1.404, 128);
  const pulse2Mat = new THREE.MeshBasicMaterial({
    color: COLOR_BLUE,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const pulse2 = new THREE.Mesh(pulse2Geo, pulse2Mat);
  group.add(pulse2);

  return {
    group,
    setVisible(v) { group.visible = v; },
    update(t, opacity, freq, rms) {
      group.visible = opacity > 0.01;
      const bins = freq.length;

      for (let i = 0; i < LISTEN_BARS; i++) {
        const bar   = bars[i]!;
        const f     = i / LISTEN_BARS;
        const binIdx = Math.min(bins - 1, Math.floor(Math.pow(f, 0.55) * bins));
        const v     = (freq[binIdx] ?? 0) / 255;
        const h     = 0.14 + v * 0.65;
        bar.scale.y = h * 9.0;
        const angle = bar.userData.angle as number;
        const r     = radius + (h - 0.05) * 0.5;
        bar.position.set(Math.cos(angle) * r, Math.sin(angle) * r, 0);
        bar.material.opacity = (0.6 + v * 0.4) * opacity;
        bar.material.color.copy(COLOR_BLUE).lerp(COLOR_CYAN, v * 1.2);
      }

      group.rotation.z = t * 0.06;

      pulseRingMat.opacity = (0.4 + rms * 0.6) * opacity;
      pulseRing.scale.setScalar(1 + rms * 0.12 + Math.sin(t * 4.0) * 0.02);

      pulse2Mat.opacity = (0.25 + rms * 0.4) * opacity;
      pulse2.scale.setScalar(1 + rms * 0.08 + Math.sin(t * 3.0 + 1) * 0.015);
    },
    dispose() {
      bars.forEach(disposeMesh);
      disposeMesh(pulseRing); disposeMesh(pulse2);
    },
  };
}

// --- THINKING: particle sphere + orbital rings --------------------

interface ParticleLayer extends Layer {
  group: THREE.Group;
  update(t: number, opacity: number): void;
}

function makeParticles(): ParticleLayer {
  const group = new THREE.Group();
  const positions = new Float32Array(PARTICLE_COUNT * 3);
  const colors    = new Float32Array(PARTICLE_COUNT * 3);

  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const u     = Math.random();
    const v     = Math.random();
    const theta = 2 * Math.PI * u;
    const phi   = Math.acos(2 * v - 1);
    const r     = 1.55 + Math.random() * 0.25;
    positions[i * 3 + 0] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);

    const accent = Math.random() < 0.1;
    const c      = accent ? COLOR_WHITE : (Math.random() < 0.4 ? COLOR_CYAN : COLOR_BLUE);
    colors[i * 3 + 0] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("color",    new THREE.BufferAttribute(colors,    3));

  const mat = new THREE.PointsMaterial({
    size: 0.04,
    vertexColors: true,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
  });

  const points = new THREE.Points(geo, mat);
  group.add(points);

  // Two orbital rings tilted at different angles
  const makeOrbRing = (radius: number, tiltX: number, tiltZ: number, color: THREE.Color, op: number) => {
    const rg = new THREE.RingGeometry(radius, radius + 0.004, 128);
    const rm = new THREE.MeshBasicMaterial({
      color: color.clone(),
      transparent: true,
      opacity: op,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(rg, rm);
    mesh.rotation.x = tiltX;
    mesh.rotation.z = tiltZ;
    return mesh;
  };

  const orb1 = makeOrbRing(1.62, Math.PI / 4,  0.3,          COLOR_CYAN, 0.5);
  const orb2 = makeOrbRing(1.62, -Math.PI / 5, -Math.PI / 6, COLOR_BLUE, 0.35);
  const orb3 = makeOrbRing(1.4,  Math.PI / 2,  0,            COLOR_BLUE, 0.25);
  group.add(orb1, orb2, orb3);

  return {
    group,
    setVisible(v) { group.visible = v; },
    update(t, opacity) {
      group.visible = opacity > 0.01;
      points.rotation.y = t * 1.2;
      points.rotation.x = Math.sin(t * 0.5) * 0.25;
      const breath = 1 + Math.sin(t * 2.8) * 0.03;
      points.scale.setScalar(breath);
      mat.opacity = 0.85 * opacity;

      orb1.rotation.y = t * 0.6;
      orb2.rotation.y = -t * 0.45;
      orb3.rotation.z = t * 0.35;

      const om1 = orb1.material as THREE.MeshBasicMaterial;
      const om2 = orb2.material as THREE.MeshBasicMaterial;
      const om3 = orb3.material as THREE.MeshBasicMaterial;
      om1.opacity = 0.5 * opacity;
      om2.opacity = 0.35 * opacity;
      om3.opacity = 0.25 * opacity;
    },
    dispose() {
      geo.dispose(); mat.dispose();
      [orb1, orb2, orb3].forEach(disposeMesh);
    },
  };
}

// --- SPEAKING: circular waveform bars around core -----------------

interface SpeakLayer extends Layer {
  group: THREE.Group;
  update(t: number, opacity: number, wave: Uint8Array, rms: number): void;
}

function makeSpeakCircle(): SpeakLayer {
  const group = new THREE.Group();
  const radius = 1.1;
  const bars: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>[] = [];

  for (let i = 0; i < SPEAK_BARS; i++) {
    const geo = new THREE.PlaneGeometry(0.018, 0.04);
    const mat = new THREE.MeshBasicMaterial({
      color: COLOR_BLUE.clone(),
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const bar  = new THREE.Mesh(geo, mat);
    const angle = (i / SPEAK_BARS) * Math.PI * 2;
    bar.position.set(Math.cos(angle) * radius, Math.sin(angle) * radius, 0);
    bar.rotation.z = angle - Math.PI / 2;
    bar.userData.angle = angle;
    bars.push(bar);
    group.add(bar);
  }

  // Outer solid ring that pulses with RMS
  const outerRingGeo = new THREE.RingGeometry(1.78, 1.786, 128);
  const outerRingMat = new THREE.MeshBasicMaterial({
    color: COLOR_CYAN,
    transparent: true,
    opacity: 0,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
  const outerRing = new THREE.Mesh(outerRingGeo, outerRingMat);
  group.add(outerRing);

  // Base ring at the waveform radius
  const baseGeo = new THREE.RingGeometry(1.09, 1.093, 128);
  const baseMat = new THREE.MeshBasicMaterial({
    color: COLOR_BLUE,
    transparent: true,
    opacity: 0,
    side: THREE.DoubleSide,
  });
  const baseRing = new THREE.Mesh(baseGeo, baseMat);
  group.add(baseRing);

  return {
    group,
    setVisible(v) { group.visible = v; },
    update(t, opacity, wave, rms) {
      group.visible = opacity > 0.01;
      const samplesPerBar = Math.max(1, Math.floor(wave.length / SPEAK_BARS));

      for (let i = 0; i < SPEAK_BARS; i++) {
        let peak = 0;
        for (let j = 0; j < samplesPerBar; j++) {
          const s = wave[i * samplesPerBar + j];
          if (s === undefined) continue;
          const dev = Math.abs(s - 128);
          if (dev > peak) peak = dev;
        }
        const v   = Math.min(1, peak / 75);
        const h   = 0.04 + v * 0.7;
        const bar = bars[i]!;
        bar.scale.y = h;
        const angle = bar.userData.angle as number;
        const r     = radius + (h - 0.02) * 0.4;
        bar.position.set(Math.cos(angle) * r, Math.sin(angle) * r, 0);
        bar.material.opacity = (0.5 + v * 0.5) * opacity;
        bar.material.color.copy(COLOR_BLUE).lerp(COLOR_CYAN, v * 0.8);
      }

      group.rotation.z = t * 0.05;
      outerRingMat.opacity  = (0.4 + rms * 0.6) * opacity;
      outerRing.scale.setScalar(1 + rms * 0.06 + Math.sin(t * 6.0) * 0.01 * rms);
      baseMat.opacity = 0.3 * opacity;
    },
    dispose() {
      bars.forEach(disposeMesh);
      disposeMesh(outerRing); disposeMesh(baseRing);
    },
  };
}

// ====================================================================
// Helpers
// ====================================================================

function makeArc(
  radius: number,
  thickness: number,
  color: THREE.Color,
  opacity: number,
): THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial> {
  const geo = new THREE.RingGeometry(radius, radius + thickness, 256);
  const mat = new THREE.MeshBasicMaterial({
    color: color.clone(),
    transparent: true,
    opacity,
    side: THREE.DoubleSide,
  });
  return new THREE.Mesh(geo, mat);
}

function setOpacity(mesh: THREE.Mesh, opacity: number): void {
  (mesh.material as THREE.MeshBasicMaterial).opacity = opacity;
}

function disposeMesh(mesh: THREE.Mesh): void {
  mesh.geometry.dispose();
  if (Array.isArray(mesh.material)) mesh.material.forEach(m => m.dispose());
  else mesh.material.dispose();
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function targetOpacities(state: JarvisState): LayerOpacities {
  switch (state) {
    case "IDLE":      return { rings: 1.0,  listen: 0,   think: 0,   speak: 0   };
    case "LISTENING": return { rings: 0.3,  listen: 1.0, think: 0,   speak: 0   };
    case "THINKING":  return { rings: 0.2,  listen: 0,   think: 1.0, speak: 0   };
    case "SPEAKING":  return { rings: 0.55, listen: 0,   think: 0,   speak: 1.0 };
    case "ERROR":     return { rings: 0.4,  listen: 0,   think: 0,   speak: 0   };
  }
}
