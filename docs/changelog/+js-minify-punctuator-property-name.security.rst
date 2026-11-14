``minify_js`` now raises ``ValueError`` when a punctuator stands where an object, pattern or class property name, or a
member name after ``.``, ``?.`` or ``new.``, belongs. A punctuator there used to repeat the text of an earlier token, so
its output could grow with the square of the input size.
