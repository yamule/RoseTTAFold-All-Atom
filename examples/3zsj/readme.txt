conda create -n RFAA python=3.8 -c conda-forge
conda activate RFAA
conda install -c nvidia cuda-toolkit=11.8.0
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
SE3-transformer はレポジトリ内にあるものを pip でインストール
pip install hydra-core
pip install scipy
pip install icecream
pip install assertpy
conda install conda-forge::openbabel
pip install opt-einsum 
pip install dgl -f https://data.dgl.ai/wheels/cu118/repo.html
pip install dglgo -f https://data.dgl.ai/wheels-test/repo.html
pip install e3nn
pip install deepdiff


LD_LIBRARY_PATH=${CONDA_PREFIX}/lib/python3.8/site-packages/nvidia/cuda_nvrtc/lib/ PYTHONPATH="." python rf2aa/run_inference_sep.py --config-name=protein database_params.hhdb=dummy_database/dummydb protein_inputs.A.fasta_file=../examples/3zsj.fas checkpoint_path=../weights/RFAA_paper_weights.pt job_name=testrun output_path=examples/3zsj/ +sm_inputs.B="" +sm_inputs.B.input=examples/3zsj/PRD_900004.smiles +sm_inputs.B.input_type=smiles

/home/ubuntu8/work/RFAA/RoseTTAFold-All-Atom/examples/3zsj/Conformer3D_COMPOUND_CID_6134.sdf
は 20240310 に https://pubchem.ncbi.nlm.nih.gov/compound/6134#section=GlyTouCan-Accession Downlowd 3D Conformer から DL。
SMILES は https://www.rcsb.org/ligand/PRD_900004 から取得。


