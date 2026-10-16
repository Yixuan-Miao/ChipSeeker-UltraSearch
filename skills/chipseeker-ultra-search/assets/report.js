"use strict";
(() => {
  const form = document.getElementById("filters");
  const list = document.getElementById("paper-list");
  const papers = Array.from(list.querySelectorAll(".paper"));
  const count = document.getElementById("visible-count");
  const none = document.getElementById("no-results");
  form.addEventListener("submit", event => event.preventDefault());
  const normalize = value => value.normalize("NFKC").toLocaleLowerCase().replace(/\s+/g, " ").trim();
  papers.forEach(paper => { paper.searchText = normalize(paper.dataset.search); });
  function update() {
    const words = normalize(document.getElementById("search").value).split(" ").filter(Boolean);
    const venue = document.getElementById("venue").value;
    const origin = document.getElementById("origin").value;
    const stars = Number(document.getElementById("stars").value);
    const sort = document.getElementById("sort").value;
    let visible = 0;
    papers.forEach(paper => {
      paper.hidden = !words.every(word => paper.searchText.includes(word))
        || Boolean(venue && paper.dataset.venue !== venue)
        || Boolean(origin && paper.dataset.origin !== origin)
        || Number(paper.dataset.stars) < stars;
      if (!paper.hidden) visible += 1;
    });
    const ordered = papers.slice().sort((left, right) => {
      if (sort !== "default") {
        const metric = Number(right.dataset[sort]) - Number(left.dataset[sort]);
        if (metric !== 0) return metric;
      }
      return Number(left.dataset.order) - Number(right.dataset.order);
    });
    ordered.forEach(paper => list.appendChild(paper));
    count.textContent = `${visible} / ${papers.length} 篇`;
    none.hidden = visible > 0;
  }
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  form.addEventListener("reset", () => setTimeout(update, 0));
  update();
})();
