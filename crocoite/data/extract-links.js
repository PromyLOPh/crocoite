/*	Extract links from a page
 */
let x = document.body.querySelectorAll('a[href]');
let ret = [];
let index = 0;
for( index=0; index < x.length; index++ ) {
   ret.push (x[index].href);
}
ret; /* immediately return results, for use with Runtime.evaluate() */
