Setext Heading Level 2
----------------------

Setext Heading Level 1 Is Still Parsed
=======================================

Alternate _emphasis_ with underscores and __strong underscores__.

* star bullet
* another star

Paragraph between two lists.

+ plus bullet
+ another plus

Soft-wrapped paragraph that continues
on the next line and
   on a third, indented line.

Two-space hard break  
works too.

~~~python
print("tilde fence")
~~~

    indented code block
    second line

Autolinks: <https://example.com/auto> and <hello@example.com>.

Reference link: [the spec][spec] and a collapsed one: [spec][].

[spec]: https://spec.commonmark.org/0.31.2/ "CommonMark spec"

Entities: &copy; &mdash; &#8212; &#x2014; &amp; &nbsp;end.

Unnecessary escapes: \. \! \( \) \: \@ \- \# \> in the middle.

### Closed ATX heading ###

#    Heading with extra spaces

| a  |   b |
|----|----:|
| 1  |   2 |

- [X] uppercase X task
- [ ] lowercase task

1. ordered
1. all ones
1. numbering

Trailing spaces at the end of a paragraph   

***

___

```Mermaid
graph TD
  A --> B
```

```TypeScript title="example.ts"
const answer = 42
```

Final paragraph with `code` inside.



Extra blank lines above are collapsed.
