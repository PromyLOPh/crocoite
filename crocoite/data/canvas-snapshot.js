/*	Replace canvas with image snapshot
 */
(function(){
	var canvas = document.querySelectorAll ("canvas"); 
	for (var i = 0; i < canvas.length; i++) { 
		var c = canvas[i];
		var data = c.toDataURL ();
		var parent = c.parentNode;
		var img = document.createElement ('img');
		/* copy all attributes */
		for (var i = 0; i < c.attributes.length; i++) {
			var attr = c.attributes.item(i);
			img.setAttribute (attr.nodeName, attr.nodeValue);
		}
		img.src = data;
		parent.replaceChild (img, c);
	}
}());
