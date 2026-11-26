const viewer=document.getElementById('image-viewer');
if(viewer&&typeof viewer.showModal==='function'){
  const image=document.getElementById('large-image');
  const area=document.querySelector('.zoom-scroll');
  document.querySelectorAll('.shot a').forEach(link=>link.addEventListener('click',event=>{
    event.preventDefault();image.src=link.href;image.alt=link.querySelector('img').alt;
    document.getElementById('image-caption').textContent=image.alt;
    area.classList.remove('full');document.getElementById('actual-size').textContent='原寸で見る';viewer.showModal();
  }));
  document.getElementById('close-image').addEventListener('click',()=>viewer.close());
  document.getElementById('actual-size').addEventListener('click',event=>{
    area.classList.toggle('full');event.target.textContent=area.classList.contains('full')?'画面に合わせる':'原寸で見る';
  });
  viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.close();});
}
document.querySelectorAll('.print').forEach(button=>button.addEventListener('click',()=>window.print()));
