// i2c dashboard — data binding (frozen shell, Refine tier; D-dash-6/7).
//
// Reads the inlined allowlisted model at window.__I2C__ and populates the
// shell's mount points (#portfolio / #project / #health) with tables and
// sections. Tables/text only in v0 — charts + telemetry arrive in v0.1.
//
// State strings are written via textContent (never innerHTML), so no project
// content can inject markup.
(function () {
  "use strict";

  var model = window.__I2C__ || {};
  var EMPTY = "\u2014"; // em dash for missing values

  function el(tag, text) {
    var e = document.createElement(tag);
    if (text !== undefined && text !== null && text !== "") {
      e.textContent = String(text);
    }
    return e;
  }

  function dash(v) {
    return (v === null || v === undefined || v === "") ? EMPTY : v;
  }

  function show(id) {
    var s = document.getElementById(id);
    if (s) { s.hidden = false; }
  }

  // Build a <table> from a header list and an array of row-arrays (cells are
  // rendered as text).
  function table(headers, rows) {
    var t = document.createElement("table");
    var thead = document.createElement("thead");
    var htr = document.createElement("tr");
    headers.forEach(function (h) { htr.appendChild(el("th", h)); });
    thead.appendChild(htr);
    t.appendChild(thead);
    var tbody = document.createElement("tbody");
    rows.forEach(function (row) {
      var tr = document.createElement("tr");
      row.forEach(function (cell) { tr.appendChild(el("td", cell)); });
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    return t;
  }

  function renderPortfolio(pf) {
    var body = document.getElementById("portfolio-body");
    body.appendChild(el("p", "Root: " + pf.root));
    var rows = (pf.projects || []).map(function (p) {
      return [
        p.name,
        dash(p.phase),
        p.state,
        dash(p.module),
        dash(p.next_action),
        dash(p.open_decisions),
        dash(p.error),
      ];
    });
    body.appendChild(table(
      ["Project", "Phase", "State", "Module", "Next action",
       "Open decisions", "Error"],
      rows
    ));
    show("portfolio");
  }

  function renderProject(pr) {
    var body = document.getElementById("project-body");
    body.appendChild(table(
      ["Phase", "State", "Module", "Regime"],
      [[dash(pr.phase), pr.state, dash(pr.module), dash(pr.regime)]]
    ));

    if (pr.steps && pr.steps.length) {
      body.appendChild(el("h3", "Steps"));
      body.appendChild(table(
        ["Step", "Title", "Status", "Commit"],
        pr.steps.map(function (s) {
          return [dash(s.step), s.title, s.status, dash(s.commit)];
        })
      ));
    }

    if (pr.gotchas && pr.gotchas.length) {
      body.appendChild(el("h3", "Gotchas"));
      var ul = document.createElement("ul");
      pr.gotchas.forEach(function (g) { ul.appendChild(el("li", g)); });
      body.appendChild(ul);
    }

    if (pr.open_decisions && pr.open_decisions.length) {
      body.appendChild(el("h3", "Open decisions"));
      body.appendChild(table(
        ["ID", "Title", "Status"],
        pr.open_decisions.map(function (d) {
          return [d.id, d.title, d.status];
        })
      ));
    }

    if (pr.recent_activity && pr.recent_activity.length) {
      body.appendChild(el("h3", "Recent activity"));
      body.appendChild(table(
        ["Phase", "Step", "Action", "Outcome", "Summary"],
        pr.recent_activity.map(function (a) {
          return [dash(a.phase), dash(a.step), a.action, a.outcome, a.summary];
        })
      ));
    }
    show("project");
  }

  function renderHealth(checks) {
    var body = document.getElementById("health-body");
    body.appendChild(table(
      ["Check", "Status", "Detail", "Remedy"],
      (checks || []).map(function (c) {
        return [c.name, c.status, dash(c.detail), dash(c.remedy)];
      })
    ));
  }

  var subtitle = document.getElementById("subtitle");
  if (model.mode === "portfolio" && model.portfolio) {
    document.title = "i2c portfolio dashboard";
    if (subtitle) {
      subtitle.textContent =
        "Portfolio snapshot — " +
        ((model.portfolio.projects || []).length) + " project(s).";
    }
    renderPortfolio(model.portfolio);
  } else if (model.project) {
    var pname = model.project_name;
    document.title = (pname ? pname + " — " : "") + "i2c dashboard";
    if (subtitle) {
      subtitle.textContent =
        (pname ? pname + " — single-project snapshot." : "Single-project snapshot.");
    }
    renderProject(model.project);
  }

  if (model.health) { renderHealth(model.health); }
})();
