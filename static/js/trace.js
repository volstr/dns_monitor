/* График следа реестра — ТЗ 9.2. Второе из трёх мест с клиентским кодом.

   Почему это вообще на клиенте: «Число корзин определяется шириной элемента, а не календарём…
   При изменении размера окна график перестраивается». Ширину элемента знает только браузер.

   График показывает след из отметок, а не сплошную полосу от первой даты до последней: сплошная
   полоса скрывает пропуски и вынуждает объяснять словами то, что должно быть видно.

   Цвет отметки — от **текущего** статуса адреса, а не от того, каким он был в тот день (9.2).
   Незакрашенная отметка означает, что за её период адрес не отдавался.

   Ограничение, которое стоит знать: корзины приходят суточные, поэтому масштаб «24 часа» здесь
   не предлагается — на нём отметка была бы одна. Для него нужны часовые корзины, они в базе
   есть (11.12), но в эту передачу не входят. */

(function () {
  "use strict";

  // 9.2: ширина одной отметки не менее 4–5 пикселей.
  var MARK_WIDTH = 5;
  var SCALES = [
    { id: "7", label: "7 дней", days: 7 },
    { id: "30", label: "30 дней", days: 30 },
    { id: "all", label: "Всё наблюдение", days: 0 },
  ];

  var STATUS_CLASS = {
    matched: "a",
    not_checked: "a",
    unmatched: "d",
    stale: "s",
  };

  function plural(count, one, few, many) {
    var tens = count % 100;
    if (tens >= 11 && tens <= 14) {
      return many;
    }
    switch (count % 10) {
      case 1:
        return one;
      case 2:
      case 3:
      case 4:
        return few;
      default:
        return many;
    }
  }

  function dayLabel(count) {
    return count + " " + plural(count, "сутки", "суток", "суток");
  }

  function dateOf(startIso, offset) {
    var date = new Date(startIso + "T00:00:00Z");
    date.setUTCDate(date.getUTCDate() + offset);
    return date.toISOString().slice(0, 10).split("-").reverse().join(".");
  }

  /* Раскладка: сколько отметок поместится и сколько суток приходится на одну.
     Число корзин ограничено сверху числом суток — дробить сутки не на что. */
  function layout(width, days) {
    var fits = Math.max(Math.floor(width / MARK_WIDTH), 1);
    var buckets = Math.min(fits, days);
    return { buckets: buckets, perBucket: Math.ceil(days / buckets) };
  }

  function render(root, data, scaleId) {
    var scale = SCALES.filter(function (item) {
      return item.id === scaleId;
    })[0] || SCALES[SCALES.length - 1];

    var visible = scale.days ? Math.min(scale.days, data.days) : data.days;
    var offset = data.days - visible;

    root.querySelectorAll("[data-trace-row]").forEach(function (cell) {
      var row = data.rows.filter(function (item) {
        return item.address === cell.dataset.traceRow;
      })[0];
      cell.textContent = "";
      if (!row) {
        return;
      }

      var plan = layout(cell.clientWidth || 320, visible);
      var seen = {};
      row.days.forEach(function (day) {
        if (day >= offset) {
          seen[Math.floor((day - offset) / plan.perBucket)] = true;
        }
      });

      for (var index = 0; index < plan.buckets; index += 1) {
        var mark = document.createElement("i");
        var from = offset + index * plan.perBucket;
        var to = Math.min(from + plan.perBucket - 1, data.days - 1);
        if (seen[index]) {
          mark.className = STATUS_CLASS[row.status] || "a";
        }
        mark.title =
          (from === to ? dateOf(data.start, from) : dateOf(data.start, from) + " — " + dateOf(data.start, to)) +
          ": " +
          (seen[index] ? "адрес отдавался" : "адрес не отдавался");
        cell.appendChild(mark);
      }

      var caption = root.querySelector("[data-trace-caption]");
      if (caption) {
        caption.textContent =
          dayLabel(visible) +
          " · одна отметка — " +
          (plan.perBucket === 1 ? "сутки" : dayLabel(plan.perBucket));
      }
    });

    var axis = root.querySelector("[data-trace-axis]");
    if (axis) {
      axis.querySelector(".l").textContent = dateOf(data.start, offset);
      axis.querySelector(".c").textContent = dateOf(data.start, offset + Math.floor(visible / 2));
      axis.querySelector(".r").textContent = "сегодня";
    }
  }

  function buildScales(root, data, redraw) {
    var holder = root.querySelector("[data-trace-scales]");
    if (!holder) {
      return;
    }
    holder.textContent = "";
    SCALES.forEach(function (scale) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = scale.label;
      if (scale.id === root.dataset.traceScale) {
        button.className = "on";
      }
      button.addEventListener("click", function () {
        root.dataset.traceScale = scale.id;
        buildScales(root, data, redraw);
        redraw();
      });
      holder.appendChild(button);
    });
  }

  function init() {
    document.querySelectorAll("[data-trace]").forEach(function (root) {
      var holder = document.getElementById(root.dataset.trace);
      if (!holder) {
        return;
      }
      var data = JSON.parse(holder.textContent);
      if (!data.rows.length) {
        return;
      }
      root.dataset.traceScale = root.dataset.traceScale || "all";

      var redraw = function () {
        render(root, data, root.dataset.traceScale);
      };
      buildScales(root, data, redraw);
      redraw();

      // 9.2: при изменении размера окна график перестраивается.
      if (window.ResizeObserver) {
        new ResizeObserver(redraw).observe(root);
      } else {
        window.addEventListener("resize", redraw);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
