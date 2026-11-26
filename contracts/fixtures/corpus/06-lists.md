- First bullet
- Second bullet with *emphasis*
  - Nested bullet
    - Third level bullet
  - Back to second level
- Third bullet

Ordered steps:

1. First step
2. Second step
   1. Sub-step a
   2. Sub-step b
3. Third step

A list can start at any number:

5. A list that starts at five
6. Continues at six

Lists hold other blocks:

- A bullet holding an ordered list:
  1. one
  2. two
- A bullet holding a code block:
  ```bash
  az login --identity
  ```
- A bullet holding a quote:
  > Quoted inside a list item.
- A bullet holding a table:
  | Key | Value |
  | --- | --- |
  | a | 1 |

Ten items widen the marker, so nested content moves to column four:

1. one
2. two
3. three
4. four
5. five
6. six
7. seven
8. eight
9. nine
10. ten
    - nested under ten

A step with code in an ordered list:

1. Sign in:
   ```bash
   az login

   az account show
   ```
2. Then *verify* the subscription.
