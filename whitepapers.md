---
layout: page
title: White Papers
permalink: /whitepapers/
---

<p class="bookshelf-intro">Papers I've read, each paired with a blog post where I dig into the ideas. Read the original paper, or jump into my notes.</p>

{% assign paper_posts = site.posts | where_exp: "p", "p.paper_url" %}

{% if paper_posts.size > 0 %}
<div class="paper-list-grid">
  {%- for post in paper_posts -%}
  <div class="paper-card">
    <div class="paper-card-body">
      <h3 class="paper-card-title">{{ post.paper_title | default: post.title | escape }}</h3>
      {%- if post.paper_source -%}<p class="paper-card-authors">{{ post.paper_source | escape }}</p>{%- endif -%}
      {%- if post.paper_summary -%}<p class="paper-card-summary">{{ post.paper_summary | escape }}</p>{%- endif -%}
      <div class="paper-card-links">
        <a class="paper-link paper-link--primary" href="{{ post.url | relative_url }}">Read my notes <span aria-hidden="true">&#8594;</span></a>
        <a class="paper-link paper-link--ghost" href="{{ post.paper_url }}" target="_blank" rel="noopener">Read the paper <span aria-hidden="true">&#8599;</span></a>
      </div>
    </div>
  </div>
  {%- endfor -%}
</div>
{% else %}
<p class="bookshelf-empty">No paper notes yet. Check back soon!</p>
{% endif %}
