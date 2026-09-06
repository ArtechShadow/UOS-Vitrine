"""Render library covers from saved PLY files without changing the reconstruction."""
from pathlib import Path
import sys,json,math,argparse
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def render(run):
    import torch
    from PIL import Image
    from vitrine import cuda_toolkit
    cuda_toolkit.configure()
    from gsplat import rasterization
    from vitrine.ply import read_splat_ply
    ply=run/'model/scene.ply'
    data=read_splat_ply(ply)
    def t(key):return torch.from_numpy(np.ascontiguousarray(data[key])).float().cuda()
    means=t('means');scales=t('scales').exp();quats=torch.nn.functional.normalize(t('quats'),dim=-1)
    opacity=t('opacities').sigmoid();sh=torch.cat([t('sh0'),t('shN')],dim=1)
    cameras=run/'model/viewer-cameras.json'
    pose=None
    if cameras.exists():pose=json.loads(cameras.read_text())[0]
    sparse=run/'sfm/sparse_text'
    if pose is None and (sparse/'images.txt').exists():
        from vitrine.colmap_io import read_model
        model=read_model(sparse)
        choices=[i for i in model.images if model.camera_for(i).width>model.camera_for(i).height] or model.images
        shot=choices[len(choices)//2];r=shot.rotation_matrix();pos=shot.camera_centre()
        pose={'position':pos.tolist(),'lookAt':(pos+r.T@np.array([0,0,1])).tolist(),'up':(r.T@np.array([0,-1,0])).tolist(),'verticalFov':60,'name':shot.name}
    if pose is None:
        cloud=data['means'][::max(1,len(data['means'])//80000)]
        lo,hi=np.quantile(cloud,[.05,.95],axis=0);centre=(lo+hi)/2;radius=max(float(max(hi-lo))/2,.5)
        pose={'position':(centre+np.array([0,-.2,1.35])*radius).tolist(),'lookAt':centre.tolist(),'up':[0,-1,0],'verticalFov':55,'name':'bounds overview'}
    eye=np.array(pose['position']);forward=np.array(pose['lookAt'])-eye;forward/=np.linalg.norm(forward)
    right=np.cross(forward,np.array(pose['up']));right/=np.linalg.norm(right);down=np.cross(forward,right)
    rot=np.stack([right,down,forward]);view=np.eye(4);view[:3,:3]=rot;view[:3,3]=-rot@eye
    w,h=960,600;f=h/(2*math.tan(math.radians(pose['verticalFov'])/2))
    k=np.array([[f,0,w/2],[0,f,h/2],[0,0,1]])
    with torch.no_grad():
        rgb,_,_=rasterization(means=means,quats=quats,scales=scales,opacities=opacity,colors=sh,viewmats=torch.tensor(view,dtype=torch.float32,device='cuda')[None],Ks=torch.tensor(k,dtype=torch.float32,device='cuda')[None],width=w,height=h,sh_degree=int(data['sh_degree']),packed=True,rasterize_mode='antialiased')
    out=run/'model/library-hero.jpg'
    Image.fromarray((rgb[0].clamp(0,1).cpu().numpy()*255).astype('uint8')).save(out,quality=90)
    (run/'model/library-hero.json').write_text(json.dumps({'source':'scene.ply','source_mtime_ns':ply.stat().st_mtime_ns,'source_bytes':ply.stat().st_size,'viewpoint':pose},indent=2))
    print(str(out),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('runs',nargs='+',type=Path)
    for run in parser.parse_args().runs:render(run)
