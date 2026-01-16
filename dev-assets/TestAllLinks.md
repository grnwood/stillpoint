# Link Format Test Page

This page tests all supported link formats in the wiki system.

## Wiki-Style Links with Labels

### HTTP Links
[https://github.com|GitHub]
[https://www.google.com|Google Search]
[https://example.com/some/path?query=test|Example Site with Query]

### Page Links
[:Projects:WebApp|Web Application Project]
[:Documentation:APIReference|API Reference Docs]
[:Meeting Notes:2025:January:Planning|January Planning Meeting]

### Page Links with Anchors
[:Projects:Backend#architecture|Backend Architecture Section]
[:Documentation:UserGuide#getting-started|Getting Started Guide]
[:Research:AI:MachineLearning#neural-networks|Neural Networks Research]

### Links with Spaces in Page Names
[:Journal:2025:11:18:Project Review:Status Update|Project Status]
[:Team:Engineering:Code Review Guidelines|Review Guidelines]

## Wiki-Style Links without Labels (Empty Labels)

### HTTP Links
[https://stackoverflow.com|]
[https://news.ycombinator.com|]

### Page Links
[:Tasks:TodoList|]
[:Ideas:FutureFeatures|]

### Page Links with Anchors
[:Documentation:Setup#installation|]
[:Projects:Frontend#components|]

## CamelCase Links (Relative)

+ProjectOverview
+MeetingNotes
+TaskList
+DocumentationIndex

## Plain Colon Links (Without Brackets)

:Projects:WebDevelopment
:Research:DataScience
:Documentation:TechnicalSpecs

## Plain HTTP URLs (Without Brackets)

https://www.wikipedia.org
https://github.com/example/repo
https://docs.python.org/3/

## Mixed Content Examples

Here's a sentence with a wiki link [:Projects:CurrentWork|current work] in the middle.

Check out the [https://developer.mozilla.org|MDN Web Docs] for web standards.

You can also reference +RelativePages in your text.

Visit https://example.com or see [:Reference:Guidelines|our guidelines].

## Anchors and Headings Test

### Installation Steps

Link to this section: [:TestAllLinks#installation-steps|Installation]

### Configuration Options

Link to this section: [:TestAllLinks#configuration-options|Configuration]

### Troubleshooting

Link with anchor: [:Support:FAQ#common-issues|Common Issues]

## Edge Cases

### Multiple Links in One Line
Check [:Projects:Alpha|Project Alpha] and [:Projects:Beta|Project Beta] for details.

### Links at Start and End
[:Documentation:Overview|Overview] is important. See [:Reference:Index|Index]

### Empty Label Links
Testing :NonExistent:Page and [https://test.com|] formats.

### Complex URLs
[https://example.com/path/to/resource?param1=value1&param2=value2#section|Complex URL Example]

---

End of test page. All link formats above should be properly recognized and rendered.
