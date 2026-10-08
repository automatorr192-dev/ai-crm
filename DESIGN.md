---
name: AI-CRM
description: Банкнота для малого бизнеса услуг: воронка, сделка, отчёт, где деньги напечатаны, а ИИ подписан фиолетовым
colors:
  engrave: "#22574a"
  engrave-soft: "#dbe8e2"
  engrave-ink: "#f4f8f5"
  violet: "#5d46a8"
  violet-soft: "#ece8f8"
  carmine: "#b4283f"
  carmine-soft: "#f8e4e8"
  gold: "#a8822a"
  gold-soft: "#f3ead2"
  paper: "#eef2ec"
  sheet: "#fafbf8"
  rule: "#cfd8d1"
  rule-strong: "#9fb0a6"
  ink: "#18241f"
  ink-2: "#3d4c46"
  ink-3: "#66756f"
  engrave-dark: "#7fc2a9"
  engrave-soft-dark: "#1b3129"
  engrave-ink-dark: "#0c1a15"
  violet-dark: "#ab98f2"
  violet-soft-dark: "#241d3d"
  carmine-dark: "#f07a8c"
  carmine-soft-dark: "#3a1820"
  gold-dark: "#d9b65a"
  gold-soft-dark: "#33290f"
  paper-dark: "#0f1714"
  sheet-dark: "#15201c"
  rule-dark: "#26342e"
  rule-strong-dark: "#3d5048"
  ink-dark: "#e6eee9"
  ink-2-dark: "#b9c7c0"
  ink-3-dark: "#8a9a93"
typography:
  denomination:
    fontFamily: "Old Standard, Times New Roman, serif"
    fontSize: "46px"
    fontWeight: 400
    lineHeight: 1.1
    fontFeature: "lnum, tnum"
  headline:
    fontFamily: "Old Standard, Times New Roman, serif"
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.05
    letterSpacing: "-0.005em"
  figure:
    fontFamily: "Old Standard, Times New Roman, serif"
    fontSize: "24px"
    fontWeight: 400
    lineHeight: 1.1
    fontFeature: "lnum, tnum"
  money:
    fontFamily: "Old Standard, Times New Roman, serif"
    fontSize: "17px"
    fontWeight: 400
    letterSpacing: "0.01em"
    fontFeature: "lnum, tnum"
  title:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    lineHeight: 1.5
  body:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
  card-title:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "14.5px"
    fontWeight: 600
    lineHeight: 1.3
  small:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.4
  serial:
    fontFamily: "Onest, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "11.5px"
    fontWeight: 400
    letterSpacing: "0.04em"
    fontFeature: "tnum"
rounded:
  card: "6px"
  field: "8px"
  sheet: "10px"
  tag: "12px"
  chip: "16px"
  pill: "20px"
spacing:
  sm: "10px"
  md: "16px"
  lg: "22px"
  xl: "28px"
components:
  button-solid:
    backgroundColor: "{colors.engrave}"
    textColor: "{colors.engrave-ink}"
    rounded: "{rounded.pill}"
    height: "40px"
    padding: "0 18px"
  button-ai:
    backgroundColor: "{colors.violet}"
    textColor: "#ffffff"
    rounded: "{rounded.pill}"
    height: "40px"
    padding: "0 18px"
  button-outline:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    height: "40px"
    padding: "0 18px"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.chip}"
    padding: "6px 13px"
  chip-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
  deal-card:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "12px 13px"
  sheet:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "20px 22px"
  stage-active:
    backgroundColor: "{colors.engrave}"
    textColor: "{colors.engrave-ink}"
    padding: "10px 8px"
  tag:
    backgroundColor: "{colors.engrave-soft}"
    textColor: "{colors.engrave}"
    rounded: "{rounded.tag}"
    padding: "3px 10px"
  tag-high:
    backgroundColor: "{colors.carmine-soft}"
    textColor: "{colors.carmine}"
  top-bar:
    backgroundColor: "{colors.engrave}"
    textColor: "{colors.engrave-ink}"
    height: "60px"
---

# Design System: AI-CRM

## Overview

**Creative North Star: "Банкнота"**

CRM устроена как денежный знак: гравюрная зелень, тонкие линии, серийные номера на каждой сделке и суммы, напечатанные антиквой, как номинал. Деньги в этой системе главные, поэтому у них свой шрифт, а всё остальное набрано спокойным гротеском и не спорит с цифрами.

ИИ здесь подписан фиолетовым, как защитная нить в купюре: шанс сделки, следующий шаг, ответы «Спроси CRM», строки истории, которые записал ассистент. Человек и фирма говорят гравюрной зеленью, машина фиолетовым, и это видно с первого взгляда. Шанс сделки рисуется гильош-розеткой: чем выше шанс, тем плотнее узор.

Плотность рабочая: доска на пять стадий, карточки сделок на светлом листе поверх бумажного фона, отчёт с полосой показателей, где главный показатель напечатан на гравюрной плашке.

**Key Characteristics:**
- Гравюрная зелень как голос фирмы: верхняя панель, главная кнопка, выбранная стадия, главный показатель отчёта
- Фиолетовый только для ИИ
- Все суммы антиквой Old Standard с моноширинными цифрами
- Серийный номер на каждой сделке
- Гильош-розетка для шанса сделки
- Двойная линия под заголовками колонок воронки

## Colors

Холодная бумага с зелёной гравюрой, одна фиолетовая нить для ИИ и три смысловых цвета: кармин, золото, гравюра.

### Primary
- **Гравюрная зелень** (engrave): верхняя панель, главная кнопка, активная стадия, главный показатель отчёта, кольцо фокуса, серийные номера.
- **Светлая гравюра** (engrave-soft): метки темы, кружки с инициалами, фон полос воронки.
- **Бумага под гравюрой** (engrave-ink): текст и значки на гравюрной зелени.

### Secondary
- **Защитная нить** (violet): всё, что сделал ИИ, — шанс сделки, следующий шаг, ответ CRM, пометка «горячий», строки истории от ассистента, кнопка отправки черновика.
- **Нить на бумаге** (violet-soft): фон панели следующего шага, блока «Спросили CRM» и реплик ассистента в переписке.

### Tertiary
- **Кармин** (carmine, carmine-soft): просрочка и высокая срочность — плашка тревоги над доской, пометка «просрочено», метка «высокая».
- **Золото** (gold, gold-soft): выигранные сделки — серийный номер и рамка карточки, полоса «сделка» в отчёте.

### Neutral
- **Бумага** (paper): фон страницы.
- **Лист** (sheet): карточки, панели, поля ввода.
- **Линия** (rule) и **жирная линия** (rule-strong): границы карточек, таблиц и колонок.
- **Чернила** (ink, ink-2, ink-3): основной, второстепенный и вспомогательный текст.

Тёмная тема переопределяет те же роли токенами с суффиксом `-dark` под `prefers-color-scheme: dark` и под `[data-theme="dark"]`: бумага становится ночной зеленью, гравюра и нить светлеют.

### Named Rules
**The Violet Is The Machine Rule.** Фиолетовый появляется только там, где работал ИИ. Кнопка, ссылка или метка, которые сделал человек, фиолетовыми не бывают.

**The Engraving Is The Firm Rule.** Гравюрная зелень — голос самой CRM: навигация, главное действие, выбранное состояние. Для статусов сделок она не используется.

**The Three Meanings Rule.** Кармин — только просрочка и срочность, золото — только выигрыш. Третьего значения у этих цветов нет.

## Typography

**Display Font:** Old Standard (с Times New Roman, serif)
**Body Font:** Onest (с system-ui, -apple-system, Segoe UI)

**Character:** Old Standard — антиква XIX века, в ней набраны номиналы и заголовки, как на старых купюрах. Onest — современный гротеск для всего интерфейса. Пара держится на разделении ролей: антиква отвечает за деньги и имена, гротеск — за работу.

### Hierarchy
- **Denomination** (400, 46px): главный показатель отчёта на гравюрной плашке; остальные показатели 34px.
- **Headline** (700, 34px, 1.05): заголовок экрана; имя клиента в карточке сделки 38px. На телефоне 28–30px.
- **Figure** (400, 24px): суммы в шапке воронки.
- **Money** (400, 17px, tnum): сумма в карточке сделки; в таблицах 16px, в заголовке колонки 16px.
- **Title** (650, 15px): заголовки панелей.
- **Body** (400, 15px, 1.5): текст заявки (16px, 1.6), ответы, переписка.
- **Card title** (600, 14.5px): имя клиента на карточке, заголовок колонки.
- **Small** (400, 13.5px): описание сделки, поля, история.
- **Label** (400, 12.5px): подписи показателей и таблиц, пометки.
- **Serial** (400, 11.5px, 0.04em, tnum): серийный номер сделки.

### Named Rules
**The Money Is Printed Rule.** Любая сумма набрана Old Standard с моноширинными цифрами. Интерфейсный текст антиквой не набирается никогда.

## Layout

Верхняя панель 60px на гравюрной зелени: знак, разделы, поле «Спросить CRM» в виде пилюли справа, пользователь. Контент шириной до 1400px с полями 24px.

Шапка экрана: заголовок антиквой, суммы рядом, действия прижаты вправо. Над доской плашка тревоги о просрочках, под ней фильтры-пилюли.

Воронка: пять колонок `minmax(250px, 1fr)` между двумя жирными линиями, колонки разделены тонкой вертикальной линией, у заголовка колонки двойная линия 3px. Карточки сделок идут стопкой с зазором 10px.

Карточка сделки: две колонки 1.55fr и 1fr с зазором 22px. Отчёт: полоса из четырёх показателей (главный шире), под ней две колонки 1.3fr и 1fr.

До 760px: разделы уходят строкой под панель, поиск во всю ширину, колонки воронки по 86% ширины с привязкой прокрутки, карточка и отчёт в одну колонку, показатели по два в ряд с главным на всю ширину.

Ритм отступов: 10, 16, 22, 28px.

## Elevation & Depth

Почти плоская система: глубину дают светлый лист поверх бумажного фона и линии. Тень есть только у всплывающего ответа «Спросить CRM».

### Shadow Vocabulary
- **Всплывающий ответ** (`box-shadow: 0 24px 60px -24px rgba(16, 34, 28, .55)`): ответ CRM под полем поиска.

## Shapes

Три уровня скругления по роли: карточки, плашки и переключатель стадий 6px; поле черновика 8px; листы-панели и полоса показателей 10px. Всё, что нажимается и фильтрует, — пилюли: кнопки 20px, фильтры 16px, метки 12px. Круглые только инициалы и точки «за» и «против» у шанса. Гильош-розетка — единственная сложная форма, она строится как гипотрохоида и всегда означает шанс сделки.

## Components

### Buttons
- **Shape:** пилюля (20px), высота 40px.
- **Solid:** гравюрная зелень, текст бумажный. Главное действие экрана.
- **AI:** фиолетовая, белый текст (в тёмной теме тёмный). Только действия с результатом ИИ: «Отправить в Telegram» под черновиком.
- **Outline:** лист с жирной линией; при наведении линия становится гравюрной.
- **Press:** сжатие до 0.98.

### Chips
- **Style:** пилюля 16px с тонкой линией, прозрачная, текст ink-2.
- **State:** выбранный фильтр залит чернилами, текст цвета листа.

### Cards / Containers
- **Карточка сделки:** лист, тонкая линия, 6px. Сверху серийный номер гравюрой, имя, описание, внизу сумма антиквой и инициалы ответственного. Выигранная — золотой номер и золотистая рамка.
- **Панель:** лист, тонкая линия, 10px, отступ 20px 22px.
- **Панель ИИ:** фон защитной нити и фиолетовая рамка — следующий шаг и «Спросили CRM».

### Inputs / Fields
- **Поиск в панели:** пилюля 38px на полупрозрачной гравюре; в фокусе становится листом с тёмным текстом, значок поиска окрашивается в фиолетовый.
- **Поля сделки:** лист, жирная линия, 6px, высота 36px.
- **Черновик ИИ:** лист, фиолетовая рамка, 8px; в фокусе рамка фиолетовая.

### Navigation
- **Верхняя панель:** разделы 14.5px на гравюре с прозрачностью .78; активный раздел без прозрачности, на светлой подложке, 550. На телефоне разделы прокручиваются строкой под панелью.

### Гильош-розетка (signature)
SVG из нескольких гипотрохоид фиолетовым и кольца с пунктиром по доле шанса. На карточке 34px рядом с процентом и причиной, в карточке сделки 84px рядом с процентом антиквой и списком «за» и «против», на главном показателе отчёта — крупный полупрозрачный узор в углу.

### Серийный номер (signature)
Номер сделки 11.5px с разрядкой 0.04em гравюрной зеленью над именем клиента, у выигранных — золотом. В карточке сделки 13px.

### Колонка воронки (signature)
Заголовок колонки с названием, числом и суммой антиквой, под ним двойная линия 3px. Колонки разделены тонкими вертикальными линиями без фона.

## Do's and Don'ts

### Do:
- **Do** набирать каждую сумму антиквой Old Standard с моноширинными цифрами и неразрывным пробелом (110 000 ₽).
- **Do** подписывать фиолетовым всё, что сделал ИИ, и только это.
- **Do** ставить серийный номер на каждую сделку.
- **Do** показывать шанс сделки гильош-розеткой вместе с числом и причиной, а не одним цветом.
- **Do** делать нажимаемое и фильтрующее пилюлями, а контейнеры — прямоугольниками 6–10px.

### Don't:
- **Don't** набирать интерфейсный текст, кнопки и подписи антиквой.
- **Don't** красить фиолетовым действия человека.
- **Don't** использовать кармин и золото для чего-то, кроме просрочки и выигрыша.
- **Don't** вешать тени на карточки и панели; тень только у всплывающего ответа.
- **Don't** добавлять цветные полосы статуса по краю карточки.
