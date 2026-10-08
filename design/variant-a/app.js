const NS = "http://www.w3.org/2000/svg";

function trochoid(radius, petals, depth, turn) {
  const r = radius / petals;
  const d = r * depth;
  const points = [];
  for (let i = 0; i <= 720; i++) {
    const t = (i / 720) * Math.PI * 2;
    const k = (radius - r) / r;
    const x = (radius - r) * Math.cos(t) + d * Math.cos(k * t);
    const y = (radius - r) * Math.sin(t) - d * Math.sin(k * t);
    const c = Math.cos(turn), s = Math.sin(turn);
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
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", trochoid(46 - k * 9, petals, 0.92 - k * 0.08, (Math.PI / petals) * k));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "0.7");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    path.setAttribute("opacity", String(1 - k * 0.18));
    svg.append(path);
  }
  const ring = document.createElementNS(NS, "circle");
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

function bars(svg, values, labels) {
  const width = 400, height = 90, gap = 6;
  const step = width / values.length;
  const peak = Math.max(...values);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.textContent = "";
  values.forEach((value, i) => {
    const h = Math.max(3, ((height - 22) * value) / peak);
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("x", (i * step + gap / 2).toFixed(1));
    rect.setAttribute("y", (height - 18 - h).toFixed(1));
    rect.setAttribute("width", (step - gap).toFixed(1));
    rect.setAttribute("height", h.toFixed(1));
    rect.setAttribute("rx", "3");
    rect.setAttribute("fill", "currentColor");
    rect.setAttribute("opacity", i === values.length - 1 ? "1" : "0.45");
    svg.append(rect);
    const text = document.createElementNS(NS, "text");
    text.setAttribute("x", (i * step + step / 2).toFixed(1));
    text.setAttribute("y", String(height - 4));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("font-size", "11");
    text.setAttribute("fill", "currentColor");
    text.setAttribute("opacity", "0.7");
    text.textContent = labels[i];
    svg.append(text);
  });
}

document.querySelectorAll("svg[data-bars]").forEach((svg) => {
  const [values, labels] = svg.dataset.bars.split("|");
  bars(svg, values.split(",").map(Number), labels.split(","));
});

const ask = document.querySelector(".ask");
if (ask) {
  const input = ask.querySelector("input");
  const question = ask.querySelector(".answer .q");
  input.addEventListener("focus", () => ask.classList.add("open"));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && input.value.trim()) question.textContent = input.value.trim();
    if (event.key === "Escape") { ask.classList.remove("open"); input.blur(); }
  });
  document.addEventListener("click", (event) => { if (!ask.contains(event.target)) ask.classList.remove("open"); });
}

const next = document.querySelector(".next");
if (next) {
  next.querySelector("[data-send]").addEventListener("click", () => next.classList.add("done"));
}

document.querySelectorAll(".stages button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".stages button").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
  });
});

document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    chip.parentElement.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
  });
});
