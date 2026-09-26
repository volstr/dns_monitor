/* Поле выбора группы с поиском — ТЗ 10.4. Третье и последнее место с клиентским кодом.

   Виджет **надстраивается над обычным `<select>`**, а не заменяет его разметкой с нуля. Отсюда
   два следствия, ради которых так и сделано: без этого файла форма продолжает работать штатным
   списком, и значение всегда лежит в том же поле, которое отправляется на сервер, — расхождению
   между показанным и отправленным взяться неоткуда.

   Разметка и классы — из прототипа: `.combo`, `.combo-btn`, `.combo-pop`, `.combo-opt`. */

(function () {
  "use strict";

  function build(select) {
    var combo = document.createElement("div");
    combo.className = "combo";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "combo-btn";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    var current = document.createElement("span");
    current.className = "cur";
    var caret = document.createElement("span");
    caret.className = "caret";
    caret.textContent = "▼";
    button.appendChild(current);
    button.appendChild(caret);

    var pop = document.createElement("div");
    pop.className = "combo-pop";
    pop.hidden = true;

    var search = document.createElement("input");
    search.placeholder = "Найти группу";
    search.setAttribute("aria-label", "Поиск по группам");

    var list = document.createElement("div");
    list.className = "combo-list";
    list.setAttribute("role", "listbox");

    var empty = document.createElement("div");
    empty.className = "combo-empty";
    empty.textContent = "Ничего не найдено";
    empty.hidden = true;

    pop.appendChild(search);
    pop.appendChild(list);
    pop.appendChild(empty);
    combo.appendChild(button);
    combo.appendChild(pop);

    function label() {
      var option = select.options[select.selectedIndex];
      current.textContent = option ? option.textContent.trim() : "";
    }

    function close() {
      pop.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function pick(option, node) {
      select.value = option.value;
      // Событие нужно всему, что слушает поле: и HTMX, и обычной отправке формы.
      select.dispatchEvent(new Event("change", { bubbles: true }));
      list.querySelectorAll(".combo-opt").forEach(function (item) {
        item.setAttribute("aria-selected", String(item === node));
      });
      label();
      close();
    }

    Array.prototype.forEach.call(select.options, function (option) {
      var node = document.createElement("button");
      node.type = "button";
      node.className = "combo-opt";
      node.setAttribute("role", "option");
      node.setAttribute("aria-selected", String(option.selected));

      var name = document.createElement("span");
      name.className = "nm";
      // Подпись прототипа — «имя · число доменов»; число приходит в тексте варианта.
      var parts = option.textContent.trim().split(" · ");
      name.textContent = parts[0];
      var count = document.createElement("span");
      count.className = "cn";
      count.textContent = parts.length > 1 ? parts[1] : "—";

      node.appendChild(name);
      node.appendChild(count);
      node.addEventListener("click", function () {
        pick(option, node);
      });
      list.appendChild(node);
    });

    search.addEventListener("input", function () {
      var needle = search.value.trim().toLowerCase();
      var shown = 0;
      list.querySelectorAll(".combo-opt").forEach(function (node) {
        var match = node.querySelector(".nm").textContent.toLowerCase().indexOf(needle) !== -1;
        // Именно display, а не атрибут hidden: у .combo-opt в CSS стоит display:flex, и он
        // атрибут перебивает — скрытые варианты остались бы на виду.
        node.style.display = match ? "" : "none";
        shown += match ? 1 : 0;
      });
      empty.hidden = shown !== 0;
    });

    button.addEventListener("click", function () {
      var opening = pop.hidden;
      pop.hidden = !opening;
      button.setAttribute("aria-expanded", String(opening));
      if (opening) {
        search.value = "";
        search.dispatchEvent(new Event("input"));
        search.focus();
      }
    });

    document.addEventListener("click", function (event) {
      if (!combo.contains(event.target)) {
        close();
      }
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        close();
      }
    });

    label();
    select.hidden = true;
    select.parentNode.insertBefore(combo, select);
  }

  function init() {
    document.querySelectorAll("select[data-combo]").forEach(build);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
