---
layout: page
title: BookShelf
permalink: /bookshelf/
---

<p class="bookshelf-intro">Books I've read, with my notes taken a chapter at a time. Click any book to dive into the notes.</p>

{% assign books = site.books | sort: "order" %}

{% if books.size > 0 %}
<div class="bookshelf-grid">
  {%- for book in books -%}
  <a class="book-card" href="{{ book.url | relative_url }}">
    {%- if book.cover -%}
    <div class="book-card-cover"><img src="{{ book.cover | relative_url }}" alt="Cover of {{ book.title | escape }}"></div>
    {%- else -%}
    <div class="book-card-cover book-card-cover--placeholder" aria-hidden="true"></div>
    {%- endif -%}
    <div class="book-card-body">
      <h3 class="book-card-title">{{ book.title | escape }}</h3>
      {%- if book.author -%}<p class="book-card-author">by {{ book.author | escape }}</p>{%- endif -%}
      <div class="book-card-meta">
        {%- if book.status -%}<span class="book-status book-status--{{ book.status | downcase | replace: ' ', '-' }}">{{ book.status }}</span>{%- endif -%}
        {%- if book.rating -%}<span class="book-rating" title="{{ book.rating }} / 5">{% assign r = book.rating | times: 1 %}{% for i in (1..5) %}{% if i <= r %}&#9733;{% else %}&#9734;{% endif %}{% endfor %}</span>{%- endif -%}
      </div>
      {%- if book.summary -%}<p class="book-card-summary">{{ book.summary | escape }}</p>{%- endif -%}
    </div>
  </a>
  {%- endfor -%}
</div>
{% else %}
<p class="bookshelf-empty">No books on the shelf yet. Check back soon!</p>
{% endif %}
