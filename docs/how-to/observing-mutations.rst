########################
 Observe tree mutations
########################

A :class:`~turbohtml.MutationObserver` records the changes you make to a subtree and hands them back as
:class:`~turbohtml.MutationRecord` values, the way the DOM ``MutationObserver`` does -- but synchronously, since
turbohtml has no event loop. Register a target, edit the tree, then read the records.

***************************
 Record child list changes
***************************

Pass ``child_list=True`` to record every node added to or removed from the target's own children. Read the batch with
:meth:`~turbohtml.MutationObserver.take_records`, which returns and clears the queue:

.. testcode::

    from turbohtml import Element, MutationObserver

    doc = turbohtml.parse("<ul><li>a</li></ul>")
    ul = doc.find("ul")
    observer = MutationObserver()
    observer.observe(ul, child_list=True)
    ul.append(Element("li"))
    (record,) = observer.take_records()
    print(record.type, [node.tag for node in record.added_nodes])

.. testoutput::

    childList ['li']

*****************************************
 Record attribute changes and old values
*****************************************

``attributes=True`` records attribute edits; add ``attribute_old_value=True`` to capture the value before each change,
and ``attribute_filter`` to limit which names record:

.. testcode::

    doc = turbohtml.parse("<a href='/old'>x</a>")
    link = doc.find("a")
    observer = MutationObserver()
    observer.observe(link, attributes=True, attribute_old_value=True, attribute_filter=["href"])
    link.attrs["href"] = "/new"
    link.attrs["class"] = "seen"  # filtered out
    (record,) = observer.take_records()
    print(record.attribute_name, record.old_value)

.. testoutput::

    href /old

***********************
 Watch a whole subtree
***********************

``subtree=True`` extends the watch from the target's own children to every descendant, so a change deep in the tree
still records:

.. testcode::

    doc = turbohtml.parse("<div><p><span>x</span></p></div>")
    div = doc.find("div")
    observer = MutationObserver()
    observer.observe(div, child_list=True, subtree=True)
    doc.find("span").append(Element("b"))
    (record,) = observer.take_records()
    print(record.target.tag)

.. testoutput::

    span

*******************************
 Deliver records to a callback
*******************************

The DOM schedules the callback on a microtask; with no event loop, turbohtml runs it when you call
:meth:`~turbohtml.MutationObserver.deliver`, which drains the queue and calls ``callback(records, observer)``.
:meth:`~turbohtml.MutationObserver.disconnect` stops observing and discards any pending records:

.. testcode::

    doc = turbohtml.parse("<ul></ul>")
    ul = doc.find("ul")
    observer = MutationObserver(lambda records, _obs: print(f"{len(records)} change(s)"))
    observer.observe(ul, child_list=True)
    ul.append(Element("li"))
    observer.deliver()
    observer.disconnect()

.. testoutput::

    1 change(s)
