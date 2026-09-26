/* Линейка подсети — ТЗ 11.4. Одно из трёх мест, где в этом продукте есть клиентский код.

   Требование 11.4 дословно: «при наложении меток они склеиваются в одну с числом адресов,
   позиция при этом остаётся честной. Клик по склеенной метке открывает увеличенный фрагмент
   диапазона, на котором адреса расходятся. Подписи не раздвигаются в ущерб точности позиции».

   Отсюда главное правило этого файла: позиция метки не сдвигается никогда. Если две подписи не
   помещаются рядом, они превращаются в одну — но центр склейки остаётся честным средним, а не
   удобным местом.

   Позиции приходят с сервера долями от нуля до единицы: адрес IPv6 — это 128 бит, и в числе
   JavaScript он не помещается. Здесь только раскладка. */

(function () {
  "use strict";

  // Минимальное расстояние между подписями в долях ширины. Меньше — подписи налезают.
  var MERGE_GAP = 0.06;
  var TICKS = 8;

  function parseData(element) {
    var holder = document.getElementById(element.dataset.ruler);
    return holder ? JSON.parse(holder.textContent) : null;
  }

  /* Склейка: соседние отметки, которые не расходятся по ширине, собираются в группу.
     Позиция группы — среднее позиций её членов, а не край и не округление. */
  function cluster(marks) {
    var sorted = marks.slice().sort(function (a, b) {
      return a.position - b.position;
    });
    var groups = [];
    sorted.forEach(function (mark) {
      var last = groups[groups.length - 1];
      if (last && mark.position - last.marks[last.marks.length - 1].position < MERGE_GAP) {
        last.marks.push(mark);
        last.position =
          last.marks.reduce(function (sum, item) {
            return sum + item.position;
          }, 0) / last.marks.length;
        return;
      }
      groups.push({ position: mark.position, marks: [mark] });
    });
    return groups;
  }

  function label(group) {
    if (group.marks.length === 1) {
      return group.marks[0].address;
    }
    var first = group.marks[0].address;
    var last = group.marks[group.marks.length - 1].address;
    return first + "–" + last.split(".").pop();
  }

  function tick(track, position, major) {
    var node = document.createElement("span");
    node.className = major ? "tick maj" : "tick";
    node.style.left = (position * 100).toFixed(4) + "%";
    track.appendChild(node);
  }

  function pin(track, group, onZoom) {
    var node = document.createElement(group.marks.length > 1 ? "button" : "span");
    node.className = "pin" + (group.marks.length > 1 ? " cluster" : "");
    node.style.left = (group.position * 100).toFixed(4) + "%";

    var caption = document.createElement("b");
    caption.textContent = label(group);
    var stem = document.createElement("i");
    if (group.marks.length > 1) {
      var count = document.createElement("em");
      count.textContent = String(group.marks.length);
      stem.appendChild(count);
      node.title =
        group.marks
          .map(function (mark) {
            return mark.address;
          })
          .join(", ") + " — соседние адреса";
      node.setAttribute("aria-expanded", "false");
      node.addEventListener("click", function () {
        onZoom(group, node);
      });
    } else {
      node.title = group.marks[0].address;
    }
    node.appendChild(caption);
    node.appendChild(stem);
    track.appendChild(node);
  }

  /* Увеличенный фрагмент: диапазон сужается до окрестности склейки, и внутри него те же адреса
     раскладываются заново — теперь они расходятся. */
  function zoomBlock(container, data, group) {
    var block = document.createElement("div");
    block.className = "zoom";

    var head = document.createElement("div");
    head.className = "zoom-h";
    var eyebrow = document.createElement("span");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = "Увеличено";
    var range = document.createElement("code");
    range.textContent =
      group.marks[0].address + " — " + group.marks[group.marks.length - 1].address;
    var cap = document.createElement("span");
    cap.className = "cap";
    cap.textContent = group.marks.length + " из " + data.size + " адресов диапазона";
    var close = document.createElement("button");
    close.className = "btn btn-sm";
    close.style.marginLeft = "auto";
    close.textContent = "Свернуть";
    close.addEventListener("click", function () {
      block.remove();
    });
    head.appendChild(eyebrow);
    head.appendChild(range);
    head.appendChild(cap);
    head.appendChild(close);

    var track = document.createElement("div");
    track.className = "track zoomed";
    var span = group.marks[group.marks.length - 1].position - group.marks[0].position;
    group.marks.forEach(function (mark, index) {
      var relative = span ? (mark.position - group.marks[0].position) / span : index / Math.max(group.marks.length - 1, 1);
      // Поля по краям, чтобы крайние подписи не срезались.
      pin(track, { position: 0.08 + relative * 0.84, marks: [mark] }, function () {});
    });

    block.appendChild(head);
    block.appendChild(track);
    container.appendChild(block);
    return block;
  }

  function draw(element) {
    var data = parseData(element);
    if (!data) {
      return;
    }
    var track = element.querySelector(".track");
    if (!track) {
      return;
    }
    track.textContent = "";

    for (var index = 0; index <= TICKS; index += 1) {
      tick(track, index / TICKS, index % 2 === 0);
    }

    var opened = null;
    cluster(data.marks).forEach(function (group) {
      pin(track, group, function (selected, node) {
        if (opened) {
          opened.remove();
        }
        node.setAttribute("aria-expanded", "true");
        opened = zoomBlock(element, data, selected);
      });
    });
  }

  function init() {
    document.querySelectorAll("[data-ruler]").forEach(draw);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
