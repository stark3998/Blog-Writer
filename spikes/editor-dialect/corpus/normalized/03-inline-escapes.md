Literal asterisks: 2 \* 3 \* 4 and a\*b\*c stay literal.

Underscores inside words stay bare: snake_case_name and CONSTANT_VALUE, but edge ones are escaped: \_private and trailing\_ and \_\_dunder\_\_.

Brackets are escaped: \[sic\] and \[1\] and a regex like \[^abc\] in prose.

Backticks and backslashes: \` and C:\\Users\\jatin and a trailing backslash \\

Angle brackets: a < b and c > d stay bare, but \<T> and \</div> and \<https://example.com> are escaped.

Ampersands: AT&T and R&D stay bare; \&copy; and \&#169; are escaped so they stay literal.

\# Not a heading

\- Not a list item

\+ Not a list item either

1\. Not an ordered list

2024\. A year that starts a sentence

\> Not a quote

\=== Not a setext underline

\:::note not a callout

\| not a table row

\~~~ not a fence

Hashtags at the start of a line need no escape: #security and #ai.

An exclamation mark before a link stays text: wow\![the link](https://example.com/wow).
