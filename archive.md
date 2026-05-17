---
layout: page
title: Blog Archive
---

<div class="archive-list">
{% for tag in site.tags %}
  <h3>{{ tag[0] }}</h3>
  <ul>
    {% for post in tag[1] %}
      <li><a href="{{ post.url }}">{{ post.date | date: "%B %Y" }} &mdash; {{ post.title }}</a></li>
    {% endfor %}
  </ul>
{% endfor %}
</div>
