A TypeScript block:

```ts
export function add(a: number, b: number): number {
  return a + b
}
```

No language:

```
plain text block
  with indentation preserved
```

A fence inside the code needs a longer fence:

````md
```bash
echo "nested fence"
```
````

Code that looks like dialect syntax stays verbatim:

```text
:::note
This is not a callout.
:::

A citation-looking string [^not-a-citation] and a footnote line:
[^x]: not a definition
<script>alert("not html either")</script>
```

Blank lines, tabs and trailing spaces inside code are preserved:

```python
def handler(event):
	return {"status": 200}   

    # indented comment after a blank line
```

Tilde fences inside code:

```text
~~~
still code
~~~
```

An empty code block:

```json
```

A language with symbols in its name:

```c++
int main() { return 0; }
```

Inline code: `const x = 1`, `` `backticks` `` and ``a ` b``, `  padded  `, `<b>not html</b>`, and `&amp; stays literal`.
