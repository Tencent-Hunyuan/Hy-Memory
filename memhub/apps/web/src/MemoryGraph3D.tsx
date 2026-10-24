import type {
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import { Focus, Minus, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import {
  readDisplayPreference,
  subscribeDisplayPreference,
} from "./display-preferences";
import { graphEase } from "./graph-motion";
import { useMemoryLanguage } from "./memory-language";

/** 力导向迭代总步数。原实现在挂载时一次算完，现改为分帧推进。 */
const LAYOUT_ITERATIONS = 90;
/** Soft budget checked between steps; one O(n^2) step can exceed it. */
const LAYOUT_BUDGET_MS = 6;
/** Layout work remains bounded per frame, including reduced-motion mode. */
const LAYOUT_MAX_STEPS_PER_FRAME = 3;
/** 节点交错入场的单位延迟与时长（毫秒）。 */
const ENTER_STAGGER_MS = 0;
const ENTER_DURATION_MS = 240;
const SELECT_DURATION_MS = 180;

const SELECTED_COLOR = new THREE.Color(0x294355);
const SELECTED_EMISSIVE = new THREE.Color(0x607e97);

export function MemoryGraph3D({
  graph,
  selectedNodeId,
  onSelectNode,
}: {
  graph: MemoryGraphResponse;
  selectedNodeId: string | null;
  onSelectNode(node: MemoryGraphNode): void;
}) {
  const { text } = useMemoryLanguage();
  const host = useRef<HTMLDivElement>(null);
  const labelHost = useRef<HTMLDivElement>(null);
  const selectRef = useRef(onSelectNode);
  // 选中态通过 ref 传入渲染循环，而不是进 useEffect 依赖。
  // 依赖里带 selectedNodeId 会导致每次点击重建整个场景、重跑布局、重建全部 mesh。
  const selectedRef = useRef(selectedNodeId);
  const cameraPose = useRef<{
    position: THREE.Vector3;
    target: THREE.Vector3;
  } | null>(null);
  const actionsRef = useRef<{
    zoom(scale: number): void;
    reset(): void;
  } | null>(null);
  const wake = useRef<(() => void) | null>(null);
  const positionsCache = useRef(new Map<string, THREE.Vector3>());
  const graphRef = useRef(graph);
  graphRef.current = graph;
  const graphIdentity = `${graph.nodes.map((node) => node.node_id).join("\n")}\n${graph.edges.map((edge) => edge.edge_id).join("\n")}`;
  const [available, setAvailable] = useState(true);
  const [hovered, setHovered] = useState<{
    label: string;
    x: number;
    y: number;
  } | null>(null);
  selectRef.current = onSelectNode;
  useEffect(() => {
    selectedRef.current = selectedNodeId;
    wake.current?.();
  }, [selectedNodeId]);

  useEffect(() => {
    void graphIdentity;
    const graph = graphRef.current;
    const element = host.current;
    if (!element) return;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
    } catch {
      setAvailable(false);
      return;
    }
    setAvailable(true);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    element.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");

    let reduceMotion = readDisplayPreference(
      "(prefers-reduced-motion: reduce)",
    );
    let reduceTransparency = readDisplayPreference(
      "(prefers-reduced-transparency: reduce)",
    );
    let frame: number | null = null;
    let disposed = false;
    let inView = true;
    let contextLost = false;
    const canDraw = () =>
      !disposed &&
      !contextLost &&
      inView &&
      !document.hidden &&
      element.clientWidth > 0 &&
      element.clientHeight > 0;
    const requestFrame = () => {
      if (frame === null && canDraw())
        frame = requestAnimationFrame(() => draw());
    };
    const stopFrame = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    };
    wake.current = requestFrame;

    const scene = new THREE.Scene();
    // Depth cue has to fade toward the surface the canvas actually sits on.
    // Held at the old panel black, distant nodes darkened into a background
    // that is now paper, reading as grime rather than distance.
    scene.fog = new THREE.FogExp2(GRAPH_CANVAS, 0.045);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 100);
    camera.position.set(0, 3.5, 13);
    const controls = new OrbitControls(camera, renderer.domElement);
    if (cameraPose.current) {
      camera.position.copy(cameraPose.current.position);
      controls.target.copy(cameraPose.current.target);
      controls.update();
    }
    controls.enableDamping = false;
    controls.autoRotate = false;
    controls.minDistance = 4;
    controls.maxDistance = 28;

    scene.add(new THREE.AmbientLight(0xf2f6fa, 1.6));
    const directional = new THREE.DirectionalLight(0xe3edf5, 2);
    directional.position.set(6, 8, 10);
    scene.add(directional);

    const layout = createRelationLayout(graph);
    const positions = layout.positions;
    const cachedCount = graph.nodes.filter((node) =>
      positionsCache.current.has(node.node_id),
    ).length;
    // A brand-new node used to throw away every settled position and replay
    // the entrance. Keep the nodes that were already on screen where they are.
    const returning = cachedCount > 0 && cachedCount === graph.nodes.length;
    const extending = cachedCount > 0 && cachedCount < graph.nodes.length;
    if (returning || extending) {
      for (const [id, position] of positions) {
        const cached = positionsCache.current.get(id);
        if (cached) position.copy(cached);
      }
    }
    const labels = graph.nodes
      .filter(
        (node) => node.node_type === "source" || node.node_type === "topic",
      )
      .slice(0, 4)
      .map((node) => {
        const element = document.createElement("span");
        element.textContent = node.label;
        element.className = "graph-node-label";
        labelHost.current?.append(element);
        return { node, element };
      });
    const screenPosition = new THREE.Vector3();
    const updateLabels = () => {
      const occupied: Array<{ x: number; y: number }> = [];
      for (const label of labels) {
        const position = positions.get(label.node.node_id);
        if (!position) continue;
        screenPosition.copy(position).project(camera);
        const x = ((screenPosition.x + 1) * element.clientWidth) / 2;
        const y = ((1 - screenPosition.y) * element.clientHeight) / 2 + 18;
        const hidden =
          screenPosition.z > 1 ||
          x < 60 ||
          x > element.clientWidth - 60 ||
          y < 20 ||
          y > element.clientHeight - 32 ||
          occupied.some(
            (p) => Math.abs(p.x - x) < 130 && Math.abs(p.y - y) < 30,
          );
        label.element.hidden = hidden;
        if (!hidden) {
          label.element.style.transform = `translate(${x}px, ${y}px) translateX(-50%)`;
          occupied.push({ x, y });
        }
      }
    };

    const selectable: THREE.Mesh[] = [];
    const materials = new Set<THREE.Material>();
    const geometryByType = {
      source: new THREE.SphereGeometry(0.24, 18, 12),
      memory: new THREE.SphereGeometry(0.17, 18, 12),
      schema: new THREE.IcosahedronGeometry(0.2, 1),
      topic: new THREE.SphereGeometry(0.13, 16, 10),
    };

    type NodeVisual = {
      mesh: THREE.Mesh;
      material: THREE.MeshStandardMaterial;
      baseColor: THREE.Color;
      /** 入场进度 0..1 */
      enter: number;
      /** 入场延迟（毫秒） */
      enterDelay: number;
      /** 选中权重 0..1，用于颜色与自发光插值 */
      select: number;
      selectFrom: number;
    };
    const visuals: NodeVisual[] = [];

    let index = 0;
    for (const node of graph.nodes) {
      const position = positions.get(node.node_id);
      if (!position) continue;
      const baseColor = new THREE.Color(colorForNode(node));
      const material = new THREE.MeshStandardMaterial({
        color: baseColor.clone(),
        emissive: SELECTED_EMISSIVE.clone(),
        emissiveIntensity: 0,
        roughness: node.node_type === "schema" ? 0.5 : 0.75,
        metalness: node.node_type === "schema" ? 0.18 : 0.05,
      });
      materials.add(material);
      const mesh = new THREE.Mesh(geometryByType[node.node_type], material);
      mesh.position.copy(position);
      mesh.userData.nodeId = node.node_id;
      const enter = 1;
      mesh.scale.setScalar(1);
      mesh.visible = returning || extending;
      scene.add(mesh);
      selectable.push(mesh);
      visuals.push({
        mesh,
        material,
        baseColor,
        enter,
        enterDelay: Math.min(120, index * ENTER_STAGGER_MS),
        select: 0,
        selectFrom: 0,
      });
      index += 1;
    }

    type EdgeVisual = {
      attribute: THREE.BufferAttribute;
      material: THREE.LineBasicMaterial;
      source: THREE.Vector3;
      target: THREE.Vector3;
      baseOpacity: number;
      baseColor: THREE.Color;
      /** 两端节点入场完成后边才淡入 */
      appearAfter: number;
    };
    const edgeVisuals: EdgeVisual[] = [];
    const enterDelayById = new Map(
      visuals.map((visual) => [
        String(visual.mesh.userData.nodeId),
        visual.enterDelay,
      ]),
    );

    for (const edge of graph.edges) {
      const start = positions.get(edge.source_node_id);
      const end = positions.get(edge.target_node_id);
      if (!start || !end) continue;
      const baseOpacity = edge.relation === "evidence_source" ? 0.38 : 0.46;
      const material = new THREE.LineBasicMaterial({
        color: colorForRelation(edge.relation),
        transparent: true,
        opacity: baseOpacity,
      });
      materials.add(material);
      const geometry = new THREE.BufferGeometry().setFromPoints([start, end]);
      scene.add(new THREE.Line(geometry, material));
      const attribute = geometry.getAttribute(
        "position",
      ) as THREE.BufferAttribute;
      edgeVisuals.push({
        attribute,
        material,
        source: start,
        target: end,
        baseOpacity,
        baseColor: material.color.clone(),
        appearAfter: Math.max(
          enterDelayById.get(edge.source_node_id) ?? 0,
          enterDelayById.get(edge.target_node_id) ?? 0,
        ),
      });
    }

    const nodesById = new Map(graph.nodes.map((node) => [node.node_id, node]));
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const hitAt = (event: PointerEvent) => {
      if (startedAt === null || !canDraw()) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      return raycaster.intersectObjects(selectable, false)[0];
    };
    let pressed: { x: number; y: number; id: number; moved: boolean } | null =
      null;
    const pointerDown = (event: PointerEvent) => {
      pressed =
        event.button === 0 && !pressed
          ? {
              x: event.clientX,
              y: event.clientY,
              id: event.pointerId,
              moved: false,
            }
          : null;
    };
    const selectNode = (event: PointerEvent) => {
      const start = pressed;
      pressed = null;
      if (
        !start ||
        start.moved ||
        start.id !== event.pointerId ||
        Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5
      )
        return;
      const nodeId = hitAt(event)?.object.userData.nodeId;
      const node = typeof nodeId === "string" ? nodesById.get(nodeId) : null;
      if (node) selectRef.current(node);
    };
    const hoverNode = (event: PointerEvent) => {
      if (pressed) {
        pressed.moved ||=
          Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) > 5;
        setHovered(null);
        return;
      }
      const nodeId = hitAt(event)?.object.userData.nodeId;
      const node = typeof nodeId === "string" ? nodesById.get(nodeId) : null;
      renderer.domElement.style.cursor = node ? "pointer" : "grab";
      setHovered(
        node
          ? {
              label: node.label,
              x: event.clientX - element.getBoundingClientRect().left,
              y: event.clientY - element.getBoundingClientRect().top,
            }
          : null,
      );
    };
    const leaveNode = () => setHovered(null);
    const cancelPointer = () => {
      pressed = null;
      setHovered(null);
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointerup", selectNode);
    renderer.domElement.addEventListener("pointermove", hoverNode);
    renderer.domElement.addEventListener("pointerleave", leaveNode);
    renderer.domElement.addEventListener("pointercancel", cancelPointer);

    // 按需渲染：只有画面真的变了才提交一帧。
    let dirty = true;
    const markDirty = () => {
      dirty = true;
      requestFrame();
    };
    controls.addEventListener("change", markDirty);
    actionsRef.current = {
      zoom(scale) {
        const offset = camera.position.clone().sub(controls.target);
        offset.setLength(
          Math.max(
            controls.minDistance,
            Math.min(controls.maxDistance, offset.length() * scale),
          ),
        );
        camera.position.copy(controls.target).add(offset);
        controls.update();
        markDirty();
      },
      reset() {
        camera.position.set(0, 3.5, 13);
        controls.target.set(0, 0, 0);
        controls.update();
        markDirty();
      },
    };

    const resize = () => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      markDirty();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();

    const syncGeometry = () => {
      for (const visual of visuals) {
        const position = positions.get(String(visual.mesh.userData.nodeId));
        if (position) visual.mesh.position.copy(position);
      }
      for (const edge of edgeVisuals) {
        edge.attribute.setXYZ(0, edge.source.x, edge.source.y, edge.source.z);
        edge.attribute.setXYZ(1, edge.target.x, edge.target.y, edge.target.z);
        edge.attribute.needsUpdate = true;
      }
    };
    syncGeometry();

    let startedAt: number | null =
      returning || extending ? performance.now() - ENTER_DURATION_MS : null;
    let layoutRunning = !(returning || extending) && !layout.done();
    let appliedSelection: string | null = null;
    let selectionStartedAt = performance.now();

    const applyTransparency = () => {
      renderer.setClearColor(
        reduceTransparency ? GRAPH_CANVAS : 0x000000,
        reduceTransparency ? 1 : 0,
      );
      for (const edge of edgeVisuals) {
        edge.material.color.copy(
          reduceTransparency
            ? new THREE.Color(GRAPH_CANVAS).lerp(
                edge.baseColor,
                edge.baseOpacity,
              )
            : edge.baseColor,
        );
        edge.material.transparent = !reduceTransparency;
        edge.material.opacity = reduceTransparency
          ? 1
          : !reduceMotion && startedAt === null
            ? 0
            : edge.baseOpacity;
        edge.material.needsUpdate = true;
      }
      markDirty();
    };
    const preferencesChanged = () => {
      reduceMotion = readDisplayPreference("(prefers-reduced-motion: reduce)");
      reduceTransparency = readDisplayPreference(
        "(prefers-reduced-transparency: reduce)",
      );
      if (startedAt !== null) startedAt = performance.now() - ENTER_DURATION_MS;
      selectionStartedAt = performance.now() - SELECT_DURATION_MS;
      applyTransparency();
    };
    const unsubscribeMotion = subscribeDisplayPreference(
      "(prefers-reduced-motion: reduce)",
      preferencesChanged,
    );
    const unsubscribeTransparency = subscribeDisplayPreference(
      "(prefers-reduced-transparency: reduce)",
      preferencesChanged,
    );
    const visibilityChanged = () => {
      if (!canDraw()) {
        stopFrame();
        cancelPointer();
      } else {
        if (startedAt !== null)
          startedAt = performance.now() - ENTER_DURATION_MS;
        markDirty();
      }
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    const intersection =
      typeof IntersectionObserver === "undefined"
        ? null
        : new IntersectionObserver(([entry]) => {
            inView = entry.isIntersecting;
            visibilityChanged();
          });
    intersection?.observe(element);
    const lost = (event: Event) => {
      event.preventDefault();
      contextLost = true;
      stopFrame();
      setHovered(null);
      setAvailable(false);
    };
    renderer.domElement.addEventListener("webglcontextlost", lost);

    const draw = () => {
      frame = null;
      if (!canDraw()) return;
      const now = performance.now();
      let busy = false;

      const wanted = selectedRef.current;
      if (wanted !== appliedSelection) {
        appliedSelection = wanted;
        selectionStartedAt = now;
        for (const visual of visuals) visual.selectFrom = visual.select;
        markDirty();
      }

      // Compute offscreen; present only once the initial layout is ready.
      if (layoutRunning) {
        let steps = 0;
        const budgetStart = performance.now();
        do {
          layoutRunning = layout.step();
          steps += 1;
        } while (
          layoutRunning &&
          steps < LAYOUT_MAX_STEPS_PER_FRAME &&
          performance.now() - budgetStart < LAYOUT_BUDGET_MS
        );
        if (layoutRunning) {
          requestFrame();
          return;
        }
        syncGeometry();
        startedAt = now;
        for (const visual of visuals) visual.mesh.visible = true;
        for (const [id, position] of positions)
          positionsCache.current.set(id, position.clone());
      }

      if (!reduceMotion) {
        const elapsed = now - (startedAt ?? now);
        let entering = false;
        for (const visual of visuals) {
          if (visual.enter >= 1) continue;
          const local = (elapsed - visual.enterDelay) / ENTER_DURATION_MS;
          visual.enter = Math.min(1, Math.max(0, local));
          entering = true;
        }
        let edgeFading = false;
        for (const edge of edgeVisuals) {
          if (reduceTransparency || edge.material.opacity >= edge.baseOpacity)
            continue;
          const local = (elapsed - edge.appearAfter) / ENTER_DURATION_MS;
          edge.material.opacity =
            edge.baseOpacity * Math.min(1, Math.max(0, local));
          edgeFading = true;
        }
        if (entering || edgeFading) busy = true;
      }

      const k = reduceMotion
        ? 1
        : graphEase((now - selectionStartedAt) / SELECT_DURATION_MS);
      let selecting = false;
      for (const visual of visuals) {
        const target =
          String(visual.mesh.userData.nodeId) === appliedSelection ? 1 : 0;
        visual.select = visual.selectFrom + (target - visual.selectFrom) * k;
        selecting ||= k < 1 && visual.select !== target;
        visual.mesh.scale.setScalar(1);
        visual.material.color.lerpColors(
          visual.baseColor,
          SELECTED_COLOR,
          visual.select,
        );
        visual.material.emissiveIntensity = 0.25 * visual.select;
      }
      if (selecting) busy = true;

      controls.update();
      if (busy || dirty) {
        renderer.render(scene, camera);
        updateLabels();
        dirty = false;
      }
      if (busy) requestFrame();
    };
    applyTransparency();
    requestFrame();

    return () => {
      disposed = true;
      stopFrame();
      wake.current = null;
      cameraPose.current = {
        position: camera.position.clone(),
        target: controls.target.clone(),
      };
      observer.disconnect();
      intersection?.disconnect();
      document.removeEventListener("visibilitychange", visibilityChanged);
      unsubscribeMotion();
      unsubscribeTransparency();
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      controls.removeEventListener("change", markDirty);
      actionsRef.current = null;
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointerup", selectNode);
      renderer.domElement.removeEventListener("pointermove", hoverNode);
      renderer.domElement.removeEventListener("pointerleave", leaveNode);
      renderer.domElement.removeEventListener("pointercancel", cancelPointer);
      controls.dispose();
      const geometries = new Set<THREE.BufferGeometry>(
        Object.values(geometryByType),
      );
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
          geometries.add(object.geometry);
        }
      });
      for (const material of materials) material.dispose();
      for (const geometry of geometries) geometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      for (const label of labels) label.element.remove();
    };
    // Rebuild only when membership actually changes. A new graph object with
    // the same node/edge ids used to remount WebGL on every poll.
  }, [graphIdentity]);

  return (
    <div className="memory-graph-canvas" ref={host}>
      <div className="graph-labels" ref={labelHost} aria-hidden="true" />
      {!available ? (
        <div className="memory-graph-fallback">
          <p>{text("这台设备打不开 3D 视图，已显示关联列表。")}</p>
          {graph.nodes.map((node) => (
            <button
              type="button"
              className="secondary"
              key={node.node_id}
              aria-pressed={selectedNodeId === node.node_id}
              onClick={() => onSelectNode(node)}
            >
              {node.label}
            </button>
          ))}
        </div>
      ) : null}
      {available && (
        <div
          className="memory-graph-controls"
          role="toolbar"
          aria-label={text("图谱工具")}
        >
          <button
            type="button"
            className="secondary"
            title={text("放大")}
            aria-label={text("放大图谱")}
            onClick={() => actionsRef.current?.zoom(0.8)}
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            className="secondary"
            title={text("缩小")}
            aria-label={text("缩小图谱")}
            onClick={() => actionsRef.current?.zoom(1.25)}
          >
            <Minus size={16} />
          </button>
          <button
            type="button"
            className="secondary"
            title={text("重置视角")}
            aria-label={text("重置图谱视角")}
            onClick={() => actionsRef.current?.reset()}
          >
            <Focus size={16} />
          </button>
        </div>
      )}
      {hovered ? (
        <div
          className="memory-graph-tooltip"
          style={{
            left: Math.min(
              hovered.x + 12,
              Math.max(0, (host.current?.clientWidth ?? 300) - 280),
            ),
            top: Math.max(0, hovered.y - 40),
          }}
        >
          {hovered.label}
        </div>
      ) : null}
    </div>
  );
}

/** The paper the canvas is drawn on — `--graph-canvas` in docs/DESIGN.md §2. */
const GRAPH_CANVAS = 0xedf1f5;

/* Node and edge hues, per docs/DESIGN.md §2. The previous set was mixed for a
 * near-black panel: verdigris `0x86aaa4` on paper is a pale wash. */
function colorForNode(node: MemoryGraphNode): number {
  if (node.node_type === "source") return 0x2f6b57;
  if (node.node_type === "topic") return 0xa88140;
  if (node.node_type === "schema") return 0x7d93a6;
  return 0x4e6478;
}

function colorForRelation(relation: string): number {
  if (relation === "evidence_source") return 0x91a99c;
  if (relation === "tagged_with") return 0xa88140;
  if (relation === "derived_from") return 0x7d93a6;
  return 0xbfc9d2;
}

type RelationLayout = {
  /** node_id 到位置的映射，值是可变引用，分帧推进时原地更新。 */
  positions: Map<string, THREE.Vector3>;
  /** 推进一步，返回是否还有剩余迭代。 */
  step(): boolean;
  /** 一次跑完剩余迭代，用于降低动效偏好或需要立即出终态时。 */
  settle(): void;
  done(): boolean;
}; /**
 * 3D 力导向布局，可分帧推进。
 *
 * 与原实现的差异：
 * 1. 拆成 step() 供渲染循环逐帧调用，节点因此有生长过程
 * 2. 位置与力改用按索引的数组，内层 O(n²) 循环不再走 Map 查找
 * 3. 复用 scratch 向量，不再每对节点 clone 一个 Vector3
 *
 * 初始位置仍由 seededUnit(node_id) 决定，因此同一张图每次布局结果一致，不会乱跳。
 * 导出仅供测试校验“分帧推进与一次算完结果一致”，业务代码不应直接调用。
 */
export function createRelationLayout(
  graph: MemoryGraphResponse,
): RelationLayout {
  const nodeIds = graph.nodes.map((node) => node.node_id);
  const positionList = graph.nodes.map((node, index) => {
    const angle = seededUnit(node.node_id, 0) * Math.PI * 2;
    const elevation = (seededUnit(node.node_id, 1) - 0.5) * Math.PI;
    const radius = 3.2 + (index % 5) * 0.24;
    return new THREE.Vector3(
      Math.cos(angle) * Math.cos(elevation) * radius,
      Math.sin(elevation) * radius,
      Math.sin(angle) * Math.cos(elevation) * radius,
    );
  });
  const forceList = graph.nodes.map(() => new THREE.Vector3());
  const indexById = new Map(nodeIds.map((id, index) => [id, index]));
  const positions = new Map(
    nodeIds.map((id, index) => [id, positionList[index]] as const),
  );

  const delta = new THREE.Vector3();
  const scratch = new THREE.Vector3();
  let iteration = 0;

  const step = (): boolean => {
    if (iteration >= LAYOUT_ITERATIONS) return false;
    for (const force of forceList) force.set(0, 0, 0);

    for (let left = 0; left < positionList.length; left += 1) {
      const aPosition = positionList[left];
      const aForce = forceList[left];
      for (let right = left + 1; right < positionList.length; right += 1) {
        const bPosition = positionList[right];
        const bForce = forceList[right];
        delta.copy(aPosition).sub(bPosition);
        const distanceSq = Math.max(0.12, delta.lengthSq());
        scratch
          .copy(delta)
          .normalize()
          .multiplyScalar(0.035 / distanceSq);
        aForce.add(scratch);
        bForce.sub(scratch);
      }
    }

    for (const edge of graph.edges) {
      const sourceIndex = indexById.get(edge.source_node_id);
      const targetIndex = indexById.get(edge.target_node_id);
      if (sourceIndex === undefined || targetIndex === undefined) continue;
      delta.copy(positionList[targetIndex]).sub(positionList[sourceIndex]);
      const distance = delta.length();
      const target = edge.relation === "evidence_source" ? 2.5 : 1.35;
      scratch
        .copy(delta)
        .normalize()
        .multiplyScalar((distance - target) * 0.018);
      forceList[sourceIndex].add(scratch);
      forceList[targetIndex].sub(scratch);
    }

    for (let i = 0; i < positionList.length; i += 1) {
      positionList[i].add(forceList[i]).multiplyScalar(0.995);
    }

    iteration += 1;
    return iteration < LAYOUT_ITERATIONS;
  };

  return {
    positions,
    step,
    settle() {
      while (step());
    },
    done() {
      return iteration >= LAYOUT_ITERATIONS;
    },
  };
}

function seededUnit(value: string, salt: number): number {
  let hash = 2166136261 + salt;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}
