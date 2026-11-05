import type {
  MemoryGraphNode,
  MemoryGraphResponse,
} from "@one-memory/contracts";
import { Focus, Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { useMemoryLanguage } from "./memory-language";

const GRAPH_CANVAS = 0xedf1f5;
const MEMORY_COLOR = new THREE.Color(0x4e6478);
const SELECTED_COLOR = new THREE.Color(0x294355);

/** O(n) deterministic source clusters for the complete lite raw map. */
export function createRawClusterLayout(
  graph: MemoryGraphResponse,
): Map<string, THREE.Vector3> {
  const positions = new Map<string, THREE.Vector3>();
  const sources = graph.nodes.filter((node) => node.node_type === "source");
  const sourceIds = new Set(sources.map((node) => node.node_id));
  const sourceByMemory = new Map<string, string>();
  for (const edge of graph.edges) {
    const sourceId = sourceIds.has(edge.source_node_id)
      ? edge.source_node_id
      : sourceIds.has(edge.target_node_id)
        ? edge.target_node_id
        : null;
    if (!sourceId) continue;
    const memoryId =
      edge.source_node_id === sourceId
        ? edge.target_node_id
        : edge.source_node_id;
    sourceByMemory.set(memoryId, sourceId);
  }
  const fallbackSource = sources[0]?.node_id ?? "source:unknown";
  const groups = new Map<string, MemoryGraphNode[]>();
  for (const node of graph.nodes) {
    if (node.node_type !== "memory") continue;
    const sourceId = sourceByMemory.get(node.node_id) ?? fallbackSource;
    const group = groups.get(sourceId) ?? [];
    group.push(node);
    groups.set(sourceId, group);
  }
  const clusterRing = Math.max(3.5, sources.length * 1.2);
  sources.forEach((source, sourceIndex) => {
    const angle = (sourceIndex / Math.max(1, sources.length)) * Math.PI * 2;
    const center = new THREE.Vector3(
      Math.cos(angle) * clusterRing,
      ((sourceIndex % 3) - 1) * 1.4,
      Math.sin(angle) * clusterRing,
    );
    positions.set(source.node_id, center);
    const memories = groups.get(source.node_id) ?? [];
    memories.forEach((memory, index) => {
      const direction = seededDirection(memory.node_id);
      const radius = 0.9 + Math.cbrt(index + 1) * 0.42;
      positions.set(
        memory.node_id,
        center.clone().addScaledVector(direction, radius),
      );
    });
  });
  return positions;
}

export function RawConversationGraph3D({
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
  const selectRef = useRef(onSelectNode);
  const selectedRef = useRef(selectedNodeId);
  const graphRef = useRef(graph);
  const applySelection = useRef<(() => void) | null>(null);
  const actions = useRef<{
    zoom(scale: number): void;
    reset(): void;
  } | null>(null);
  const [available, setAvailable] = useState(true);
  const [hovered, setHovered] = useState<{
    label: string;
    x: number;
    y: number;
  } | null>(null);
  selectRef.current = onSelectNode;
  graphRef.current = graph;
  const identity = useMemo(
    () =>
      `${graph.nodes
        .map((node) => `${node.node_id}\0${node.label}`)
        .join("\n")}\n${graph.edges
        .map(
          (edge) =>
            `${edge.edge_id}\0${edge.source_node_id}\0${edge.target_node_id}`,
        )
        .join("\n")}`,
    [graph],
  );

  useEffect(() => {
    selectedRef.current = selectedNodeId;
    applySelection.current?.();
  }, [selectedNodeId]);

  useEffect(() => {
    void identity;
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
    renderer.setClearColor(0x000000, 0);
    element.appendChild(renderer.domElement);
    renderer.domElement.setAttribute("aria-hidden", "true");

    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(GRAPH_CANVAS, 0.025);
    scene.add(new THREE.AmbientLight(0xf2f6fa, 1.8));
    const directional = new THREE.DirectionalLight(0xe3edf5, 2);
    directional.position.set(8, 10, 12);
    scene.add(directional);
    const camera = new THREE.PerspectiveCamera(48, 1, 0.1, 240);
    const defaultDistance = Math.max(13, Math.sqrt(graph.nodes.length) * 0.55);
    camera.position.set(0, defaultDistance * 0.25, defaultDistance);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.minDistance = 4;
    controls.maxDistance = 140;

    const positions = createRawClusterLayout(graph);
    const nodeById = new Map(graph.nodes.map((node) => [node.node_id, node]));
    const memories = graph.nodes.filter((node) => node.node_type === "memory");
    const sources = graph.nodes.filter((node) => node.node_type === "source");
    const memoryGeometry = new THREE.SphereGeometry(0.14, 10, 8);
    const memoryMaterial = new THREE.MeshStandardMaterial({
      color: MEMORY_COLOR,
      roughness: 0.78,
      metalness: 0.04,
      vertexColors: true,
    });
    const instances = new THREE.InstancedMesh(
      memoryGeometry,
      memoryMaterial,
      Math.max(1, memories.length),
    );
    instances.count = memories.length;
    const matrix = new THREE.Matrix4();
    memories.forEach((node, index) => {
      matrix.setPosition(positions.get(node.node_id) ?? new THREE.Vector3());
      instances.setMatrixAt(index, matrix);
      instances.setColorAt(index, MEMORY_COLOR);
    });
    instances.instanceMatrix.needsUpdate = true;
    if (instances.instanceColor) instances.instanceColor.needsUpdate = true;
    scene.add(instances);

    const sourceGeometry = new THREE.SphereGeometry(0.3, 16, 12);
    const sourceMeshes = sources.map((node) => {
      const material = new THREE.MeshStandardMaterial({
        color: 0x2f6b57,
        roughness: 0.65,
      });
      const mesh = new THREE.Mesh(sourceGeometry, material);
      mesh.position.copy(positions.get(node.node_id) ?? new THREE.Vector3());
      mesh.userData.nodeId = node.node_id;
      scene.add(mesh);
      return mesh;
    });

    const edgePositions = new Float32Array(graph.edges.length * 6);
    graph.edges.forEach((edge, index) => {
      const start = positions.get(edge.source_node_id);
      const end = positions.get(edge.target_node_id);
      if (!start || !end) return;
      edgePositions.set(
        [start.x, start.y, start.z, end.x, end.y, end.z],
        index * 6,
      );
    });
    const edgeGeometry = new THREE.BufferGeometry();
    edgeGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(edgePositions, 3),
    );
    const edgeMaterial = new THREE.LineBasicMaterial({
      color: 0x91a99c,
      transparent: true,
      opacity: 0.24,
    });
    scene.add(new THREE.LineSegments(edgeGeometry, edgeMaterial));

    const render = () => renderer.render(scene, camera);
    const reset = () => {
      camera.position.set(0, defaultDistance * 0.25, defaultDistance);
      controls.target.set(0, 0, 0);
      controls.update();
      render();
    };
    actions.current = {
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
        render();
      },
      reset,
    };
    applySelection.current = () => {
      memories.forEach((node, index) => {
        instances.setColorAt(
          index,
          node.node_id === selectedRef.current ? SELECTED_COLOR : MEMORY_COLOR,
        );
      });
      if (instances.instanceColor) instances.instanceColor.needsUpdate = true;
      for (const mesh of sourceMeshes) {
        const material = mesh.material as THREE.MeshStandardMaterial;
        material.color.setHex(
          mesh.userData.nodeId === selectedRef.current ? 0x294355 : 0x2f6b57,
        );
      }
      render();
    };
    applySelection.current();

    const resize = () => {
      const width = Math.max(1, element.clientWidth);
      const height = Math.max(1, element.clientHeight);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    controls.addEventListener("change", render);
    resize();

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let lastRaycast = 0;
    let pressed: { x: number; y: number; id: number; moved: boolean } | null =
      null;
    const hit = (event: PointerEvent) => {
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const match = raycaster.intersectObjects(
        [instances, ...sourceMeshes],
        false,
      )[0];
      if (!match) return null;
      if (match.object === instances && match.instanceId !== undefined)
        return memories[match.instanceId] ?? null;
      return nodeById.get(String(match.object.userData.nodeId)) ?? null;
    };
    const move = (event: PointerEvent) => {
      if (pressed)
        pressed.moved ||=
          Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) > 5;
      const now = performance.now();
      if (now - lastRaycast < 50 || pressed) return;
      lastRaycast = now;
      const node = hit(event);
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
    const pointerDown = (event: PointerEvent) => {
      pressed =
        event.button === 0
          ? {
              x: event.clientX,
              y: event.clientY,
              id: event.pointerId,
              moved: false,
            }
          : null;
    };
    const select = (event: PointerEvent) => {
      const start = pressed;
      pressed = null;
      if (!start || start.id !== event.pointerId || start.moved) return;
      const node = hit(event);
      if (node) selectRef.current(node);
    };
    const leave = () => {
      pressed = null;
      setHovered(null);
    };
    renderer.domElement.addEventListener("pointerdown", pointerDown);
    renderer.domElement.addEventListener("pointermove", move);
    renderer.domElement.addEventListener("pointerup", select);
    renderer.domElement.addEventListener("pointerleave", leave);

    return () => {
      applySelection.current = null;
      actions.current = null;
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      renderer.domElement.removeEventListener("pointerdown", pointerDown);
      renderer.domElement.removeEventListener("pointermove", move);
      renderer.domElement.removeEventListener("pointerup", select);
      renderer.domElement.removeEventListener("pointerleave", leave);
      memoryGeometry.dispose();
      memoryMaterial.dispose();
      sourceGeometry.dispose();
      for (const mesh of sourceMeshes)
        (mesh.material as THREE.Material).dispose();
      edgeGeometry.dispose();
      edgeMaterial.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [identity]);

  return (
    <div className="memory-graph-canvas" ref={host}>
      {!available ? (
        <div className="memory-graph-fallback">
          <p>{text("这台设备打不开 3D 视图，已显示关联列表。")}</p>
        </div>
      ) : (
        <div
          className="memory-graph-controls"
          role="toolbar"
          aria-label={text("图谱工具")}
        >
          <button
            type="button"
            className="secondary"
            aria-label={text("放大图谱")}
            onClick={() => actions.current?.zoom(0.8)}
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            className="secondary"
            aria-label={text("缩小图谱")}
            onClick={() => actions.current?.zoom(1.25)}
          >
            <Minus size={16} />
          </button>
          <button
            type="button"
            className="secondary"
            aria-label={text("重置图谱视角")}
            onClick={() => actions.current?.reset()}
          >
            <Focus size={16} />
          </button>
        </div>
      )}
      {hovered ? (
        <div
          className="memory-graph-tooltip"
          style={{ left: hovered.x + 12, top: Math.max(0, hovered.y - 40) }}
        >
          {hovered.label}
        </div>
      ) : null}
    </div>
  );
}

function seededDirection(value: string): THREE.Vector3 {
  const u = seededUnit(value, 0);
  const v = seededUnit(value, 1);
  const theta = u * Math.PI * 2;
  const z = v * 2 - 1;
  const radius = Math.sqrt(Math.max(0, 1 - z * z));
  return new THREE.Vector3(
    Math.cos(theta) * radius,
    z,
    Math.sin(theta) * radius,
  );
}

function seededUnit(value: string, salt: number): number {
  let hash = 2166136261 + salt;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}
