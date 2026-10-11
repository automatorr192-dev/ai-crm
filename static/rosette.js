/* Гильош-розетка, как на купюре: несколько гипотрохоид и кольцо. Чем больше доля
   в data-chance, тем плотнее узор и длиннее кольцо. */
const SVG_NS = "http://www.w3.org/2000/svg";

function trochoid(radius, petals, depth, turn) {
  const r = radius / petals;
  const d = r * depth;
  const k = (radius - r) / r;
  const c = Math.cos(turn), s = Math.sin(turn);
  const points = [];
  for (let i = 0; i <= 720; i++) {
    const t = (i / 720) * Math.PI * 2;
    const x = (radius - r) * Math.cos(t) + d * Math.cos(k * t);
    const y = (radius - r) * Math.sin(t) - d * Math.sin(k * t);
    points.push(`${(x * c - y * s).toFixed(2)},${(x * s + y * c).toFixed(2)}`);
  }
  return "M" + points.join("L") + "Z";
}

function rosette(svg, chance) {
  svg.setAttribute("viewBox", "-52 -52 104 104");
  svg.setAttribute("aria-hidden", "true");
  svg.textContent = "";
  const petals = 5 + Math.round(chance * 13);
  const layers = 1 + Math.round(chance * 3);
  for (let k = 0; k < layers; k++) {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", trochoid(46 - k * 9, petals, 0.92 - k * 0.08, (Math.PI / petals) * k));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "0.7");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    path.setAttribute("opacity", String(1 - k * 0.18));
    svg.append(path);
  }
  const ring = document.createElementNS(SVG_NS, "circle");
  const length = 2 * Math.PI * 50;
  ring.setAttribute("r", "50");
  ring.setAttribute("fill", "none");
  ring.setAttribute("stroke", "currentColor");
  ring.setAttribute("stroke-width", "1.6");
  ring.setAttribute("vector-effect", "non-scaling-stroke");
  ring.setAttribute("stroke-dasharray", `${(length * chance).toFixed(1)} ${length.toFixed(1)}`);
  ring.setAttribute("transform", "rotate(-90)");
  svg.append(ring);
}

document.querySelectorAll("svg[data-chance]").forEach((svg) => rosette(svg, Number(svg.dataset.chance)));
