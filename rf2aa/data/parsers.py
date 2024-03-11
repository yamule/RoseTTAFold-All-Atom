import numpy as np
import scipy
import scipy.spatial
import string
import os,re
from os.path import exists
import random
import rf2aa.util as util
import gzip
import rf2aa
from rf2aa.ffindex import *
import torch
from openbabel import openbabel
from rf2aa.chemical import ChemicalData as ChemData

def get_dislf(seq, xyz, mask):
    L = seq.shape[0]
    resolved_cys_mask = ((seq==ChemData().aa2num['CYS']) * mask[:,5]).nonzero().squeeze(-1)  # cys[5]=='sg'
    sgs = xyz[resolved_cys_mask,5]
    ii,jj = torch.triu_indices(sgs.shape[0],sgs.shape[0],1)
    d_sg_sg = torch.linalg.norm(sgs[ii,:]-sgs[jj,:], dim=-1)
    is_dslf = (d_sg_sg>1.7)*(d_sg_sg<2.3)

    dslf = []
    for i in is_dslf.nonzero():
        dslf.append( (
            resolved_cys_mask[ii[i]].item(),
            resolved_cys_mask[jj[i]].item(),
        ) )
    return dslf

def read_template_pdb(L, pdb_fn, target_chain=None):
    # get full sequence from given PDB
    seq_full = list()
    prev_chain=''
    with open(pdb_fn) as fp:
        for line in fp:
            if line[:4] != "ATOM":
                continue
            if line[12:16].strip() != "CA":
                continue
            if line[21] != prev_chain:
                if len(seq_full) > 0:
                    L_s.append(len(seq_full)-offset)
                    offset = len(seq_full)
            prev_chain = line[21]
            aa = line[17:20]
            seq_full.append(ChemData().aa2num[aa] if aa in ChemData().aa2num.keys() else 20)

    seq_full = torch.tensor(seq_full).long()

    xyz = torch.full((L, 36, 3), np.nan).float()
    seq = torch.full((L,), 20).long()
    conf = torch.zeros(L,1).float()
    
    with open(pdb_fn) as fp:
        for line in fp:
            if line[:4] != "ATOM":
                continue
            resNo, atom, aa = int(line[22:26]), line[12:16], line[17:20]
            aa_idx = ChemData().aa2num[aa] if aa in ChemData().aa2num.keys() else 20
            #
            idx = resNo - 1
            for i_atm, tgtatm in enumerate(ChemData().aa2long[aa_idx]):
                if tgtatm == atom:
                    xyz[idx, i_atm, :] = torch.tensor([float(line[30:38]), float(line[38:46]), float(line[46:54])])
                    break
            seq[idx] = aa_idx
    
    mask = torch.logical_not(torch.isnan(xyz[:,:3,0])) # (L, 3)
    mask = mask.all(dim=-1)[:,None]
    conf = torch.where(mask, torch.full((L,1),0.1), torch.zeros(L,1)).float()
    seq_1hot = torch.nn.functional.one_hot(seq, num_classes=32).float()
    t1d = torch.cat((seq_1hot, conf), -1)

    #return seq_full[None], ins[None], L_s, xyz[None], t1d[None]
    return xyz[None], t1d[None]

def read_multichain_pdb(pdb_fn, tmpl_chain=None, tmpl_conf=0.1):
    print ('read_multichain_pdb',tmpl_chain)

    # get full sequence from PDB
    seq_full = list()
    L_s = list()
    prev_chain=''
    offset = 0
    with open(pdb_fn) as fp:
        for line in fp:
            if line[:4] != "ATOM":
                continue
            if line[12:16].strip() != "CA":
                continue
            if line[21] != prev_chain:
                if len(seq_full) > 0:
                    L_s.append(len(seq_full)-offset)
                    offset = len(seq_full)
            prev_chain = line[21]
            aa = line[17:20]
            seq_full.append(ChemData().aa2num[aa] if aa in ChemData().aa2num.keys() else 20)
    L_s.append(len(seq_full) - offset)

    seq_full = torch.tensor(seq_full).long()
    L = len(seq_full)
    msa = torch.stack((seq_full,seq_full,seq_full), dim=0)
    msa[1,:L_s[0]] = 20
    msa[2,L_s[0]:] = 20
    ins = torch.zeros_like(msa)

    xyz = ChemData().INIT_CRDS.reshape(1,1,ChemData().NTOTAL,3).repeat(1,L,1,1) + torch.rand(1,L,1,3)*5.0
    xyz_t = ChemData().INIT_CRDS.reshape(1,1,ChemData().NTOTAL,3).repeat(1,L,1,1) + torch.rand(1,L,1,3)*5.0

    mask = torch.full((1, L, ChemData().NTOTAL), False)
    mask_t = torch.full((1, L, ChemData().NTOTAL), False)
    seq = torch.full((1, L,), 20).long()
    conf = torch.zeros(1, L,1).float()

    with open(pdb_fn) as fp:
        for line in fp:
            if line[:4] != "ATOM":
                continue
                outbatch = 0
            
            resNo, atom, aa = int(line[22:26]), line[12:16], line[17:20]
            aa_idx = ChemData().aa2num[aa] if aa in ChemData().aa2num.keys() else 20

            idx = resNo - 1

            for i_atm, tgtatm in enumerate(ChemData().aa2long[aa_idx]):
                if tgtatm == atom:
                    xyz_i = torch.tensor([float(line[30:38]), float(line[38:46]), float(line[46:54])])
                    xyz[0, idx, i_atm, :] = xyz_i
                    mask[0, idx, i_atm] = True
                    if line[21] == tmpl_chain:
                        xyz_t[0, idx, i_atm, :] = xyz_i
                        mask_t[0, idx, i_atm] = True
                    break
            seq[0, idx] = aa_idx

    if (mask_t.any()):
        xyz_t[0] = rf2aa.util.center_and_realign_missing(xyz[0], mask[0])

    dslf = get_dislf(seq[0], xyz[0], mask[0])

    # assign confidence 'CONF' to all residues with backbone in template
    conf = torch.where(mask_t[...,:3].all(dim=-1)[...,None], torch.full((1,L,1),tmpl_conf), torch.zeros(L,1)).float()

    seq_1hot = torch.nn.functional.one_hot(seq, num_classes=ChemData().NAATOKENS-1).float()
    t1d = torch.cat((seq_1hot, conf), -1)

    return msa, ins, L_s, xyz_t, mask_t, t1d, dslf

def parse_fasta(filename,  maxseq=10000, rmsa_alphabet=False):
    msa = []
    ins = []

    fstream = open(filename,"r")

    for line in fstream:
        # skip labels
        if line[0] == '>':
            continue
            
        # remove right whitespaces
        line = line.rstrip()

        if len(line) == 0:
            continue

        # remove lowercase letters and append to MSA
        msa.append(line)

        # sequence length
        L = len(msa[-1])

        i = np.zeros((L))
        ins.append(i)

    # convert letters into numbers
    if rmsa_alphabet:
        alphabet = np.array(list("00000000000000000000-000000ACGTN"), dtype='|S1').view(np.uint8)
    else:
        alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-0acgtxbdhuy"), dtype='|S1').view(np.uint8)
    msa = np.array([list(s) for s in msa], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa[msa == alphabet[i]] = i

    ins = np.array(ins, dtype=np.uint8)

    return msa,ins

# Parse a fasta file containing multiple chains separated by '/'
def parse_multichain_fasta(filename,  maxseq=10000, rna_alphabet=False, dna_alphabet=False):
    msa = []
    ins = []

    fstream = open(filename,"r")
    table = str.maketrans(dict.fromkeys(string.ascii_lowercase))

    L_s = []
    for line in fstream:
        # skip labels
        if line[0] == '>':
            continue

        # remove right whitespaces
        line = line.rstrip()

        if len(line) == 0:
            continue

        # remove lowercase letters and append to MSA
        msa_i = line.translate(table)
        msa_i = msa_i.replace('B','D') # hacky...
        if L_s == []:
            L_s = [len(x) for x in msa_i.split('/')]
        msa_i = msa_i.replace('/','')
        msa.append(msa_i)

        # sequence length
        L = len(msa[-1])

        i = np.zeros((L))
        ins.append(i)

        if (len(msa) >= maxseq):
            break

    # convert letters into numbers
    if rna_alphabet:
        alphabet = np.array(list("00000000000000000000-000000ACGTN"), dtype='|S1').view(np.uint8)
    elif dna_alphabet:
        alphabet = np.array(list("00000000000000000000-0ACGTD00000"), dtype='|S1').view(np.uint8)
    else:
        alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-Xacgtxbdhuy"), dtype='|S1').view(np.uint8)
    msa = np.array([list(s) for s in msa], dtype='|S1').view(np.uint8)

    for i in range(alphabet.shape[0]):
        msa[msa == alphabet[i]] = i

    ins = np.array(ins, dtype=np.uint8)

    return msa,ins,L_s

#fd - parse protein/RNA coupled fastas
def parse_mixed_fasta(filename,  maxseq=10000):
    msa1,msa2 = [],[]

    fstream = open(filename,"r")
    table = str.maketrans(dict.fromkeys(string.ascii_lowercase))

    unpaired_r, unpaired_p = 0, 0

    for line in fstream:
        # skip labels
        if line[0] == '>':
            continue

        # remove right whitespaces
        line = line.rstrip()

        if len(line) == 0:
            continue

        # remove lowercase letters and append to MSA
        msa_i = line.translate(table)
        msa_i = msa_i.replace('B','D') # hacky...

        msas_i = msa_i.split('/')

        if (len(msas_i)==1):
            msas_i = [msas_i[0][:len(msa1[0])], msas_i[0][len(msa1[0]):]]

        if (len(msa1)==0 or (
            len(msas_i[0])==len(msa1[0]) and len(msas_i[1])==len(msa2[0])
        )):
            # skip if we've already found half of our limit in unpaired protein seqs
            if sum([1 for x in msas_i[1] if x != '-']) == 0:
                unpaired_p += 1
                if unpaired_p > maxseq // 2:
                    continue

            # skip if we've already found half of our limit in unpaired rna seqs
            if sum([1 for x in msas_i[0] if x != '-']) == 0:
                unpaired_r += 1
                if unpaired_r > maxseq // 2:
                    continue

            msa1.append(msas_i[0])
            msa2.append(msas_i[1])
        else:
            print ("Len error",filename, len(msas_i[0]),len(msa1[0]),len(msas_i[1]),len(msas_i[1]))

        if (len(msa1) >= maxseq):
            break

    # convert letters into numbers
    alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-Xacgtxbdhuy"), dtype='|S1').view(np.uint8)
    msa1 = np.array([list(s) for s in msa1], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa1[msa1 == alphabet[i]] = i
    msa1[msa1>=31] = 21  # anything unknown to 'X'

    alphabet = np.array(list("00000000000000000000-000000ACGTN"), dtype='|S1').view(np.uint8)
    msa2 = np.array([list(s) for s in msa2], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa2[msa2 == alphabet[i]] = i
    msa2[msa2>=31] = 30  # anything unknown to 'N'

    msa = np.concatenate((msa1,msa2),axis=-1)

    ins = np.zeros(msa.shape, dtype=np.uint8)

    return msa,ins


# parse a fasta alignment IF it exists
# otherwise return single-sequence msa
def parse_fasta_if_exists(seq, filename, maxseq=10000, rmsa_alphabet=False):
    if (exists(filename)):
        return parse_fasta(filename, maxseq, rmsa_alphabet)
    else:
        alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-0acgtxbdhuy"), dtype='|S1').view(np.uint8) # -0 are UNK/mask
        seq = np.array([list(seq)], dtype='|S1').view(np.uint8)
        for i in range(alphabet.shape[0]):
            seq[seq == alphabet[i]] = i

        return (seq, np.zeros_like(seq))


#fd - parse protein/RNA coupled fastas
def parse_mixed_fasta(filename,  maxseq=8000):
    msa1,msa2 = [],[]

    fstream = open(filename,"r")
    table = str.maketrans(dict.fromkeys(string.ascii_lowercase))

    unpaired_r, unpaired_p = 0, 0

    for line in fstream:
        # skip labels
        if line[0] == '>':
            continue
            
        # remove right whitespaces
        line = line.rstrip()

        if len(line) == 0:
            continue

        # remove lowercase letters and append to MSA
        msa_i = line.translate(table)
        msa_i = msa_i.replace('B','D') # hacky...

        msas_i = msa_i.split('/')

        if (len(msas_i)==1):
            msas_i = [msas_i[0][:len(msa1[0])], msas_i[0][len(msa1[0]):]]

        if (len(msa1)==0 or (
            len(msas_i[0])==len(msa1[0]) and len(msas_i[1])==len(msa2[0])
        )):
            # skip if we've already found half of our limit in unpaired protein seqs
            if sum([1 for x in msas_i[1] if x != '-']) == 0:
                unpaired_p += 1
                if unpaired_p > maxseq // 2:
                    continue

            # skip if we've already found half of our limit in unpaired rna seqs
            if sum([1 for x in msas_i[0] if x != '-']) == 0:
                unpaired_r += 1
                if unpaired_r > maxseq // 2:
                    continue

            msa1.append(msas_i[0])
            msa2.append(msas_i[1])
        else:
            print ("Len error",filename, len(msas_i[0]),len(msa1[0]),len(msas_i[1]),len(msas_i[1]))

        if (len(msa1) >= maxseq):
            break

    # convert letters into numbers
    alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-Xacgtxbdhuy"), dtype='|S1').view(np.uint8)
    msa1 = np.array([list(s) for s in msa1], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa1[msa1 == alphabet[i]] = i
    msa1[msa1>=31] = 21  # anything unknown to 'X'

    alphabet = np.array(list("00000000000000000000-000000ACGTN"), dtype='|S1').view(np.uint8)
    msa2 = np.array([list(s) for s in msa2], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa2[msa2 == alphabet[i]] = i
    msa2[msa2>=31] = 30  # anything unknown to 'N'

    msa = np.concatenate((msa1,msa2),axis=-1)

    ins = np.zeros(msa.shape, dtype=np.uint8)

    return msa,ins


# read A3M and convert letters into
# integers in the 0..20 range,
# also keep track of insertions
def parse_a3m(filename, maxseq=8000, paired=False):
    msa = []
    ins = []
    taxIDs = []
    table = str.maketrans(dict.fromkeys(string.ascii_lowercase))

    # read file line by line
    if filename.split('.')[-1] == 'gz':
        fstream = gzip.open(filename, 'rt')
    else:
        fstream = open(filename, 'r')

    for i, line in enumerate(fstream):
        
        # skip labels
        if line[0] == '>':
            if paired: # paired MSAs only have a TAXID in the fasta header
                taxIDs.append(line[1:].strip())
            else: # unpaired MSAs have all the metadata so use regex to pull out TAXID
                if i == 0:
                    taxIDs.append("query")
                else:
                    match = re.search( r'TaxID=(\d+)', line)
                    if match:
                        taxIDs.append(match.group(1))
                    else:
                        taxIDs.append("") # query sequence
            continue
            
        # remove right whitespaces
        line = line.rstrip()

        if len(line) == 0:
            continue

        # remove lowercase letters and append to MSA
        msa.append(line.translate(table))

        # sequence length
        L = len(msa[-1])

        # 0 - match or gap; 1 - insertion
        a = np.array([0 if c.isupper() or c=='-' else 1 for c in line])
        i = np.zeros((L))

        if np.sum(a) > 0:
            # positions of insertions
            pos = np.where(a==1)[0]

            # shift by occurrence
            a = pos - np.arange(pos.shape[0])

            # position of insertions in cleaned sequence
            # and their length
            pos,num = np.unique(a, return_counts=True)

            # append to the matrix of insetions
            i[pos] = num

        ins.append(i)

        if (len(msa) >= maxseq):
            break

    # convert letters into numbers
    alphabet = np.array(list("ARNDCQEGHILKMFPSTWYV-"), dtype='|S1').view(np.uint8)
    msa = np.array([list(s) for s in msa], dtype='|S1').view(np.uint8)
    for i in range(alphabet.shape[0]):
        msa[msa == alphabet[i]] = i

    # treat all unknown characters as gaps
    msa[msa > 20] = 20

    ins = np.array(ins, dtype=np.uint8)

    return msa,ins, np.array(taxIDs)


# read and extract xyz coords of N,Ca,C atoms
# from a PDB file
def parse_pdb(filename, seq=False, lddt_mask=False):
    lines = open(filename,'r').readlines()
    if seq:
        return parse_pdb_lines_w_seq(lines, lddt_mask=lddt_mask)
    return parse_pdb_lines(lines)

def parse_pdb_lines_w_seq(lines, lddt_mask=False):

    # indices of residues observed in the structure
    res = [(l[21:22].strip(), l[22:26],l[17:20], l[60:66].strip()) for l in lines if l[:4]=="ATOM" and l[12:16].strip() in ["CA", "P"]] # (chain letter, res num, aa)
    pdb_idx_s = [(r[0], int(r[1])) for r in res]
    idx_s = [int(r[1]) for r in res]
    plddt = [float(r[3]) for r in res]
    seq = [ChemData().aa2num[r[2]] if r[2] in ChemData().aa2num.keys() else 20 for r in res]

    # 4 BB + up to 10 SC atoms
    xyz = np.full((len(idx_s), ChemData().NTOTAL, 3), np.nan, dtype=np.float32)
    for l in lines:
        if l[:4] != "ATOM":
            continue
        chain, resNo, atom, aa = l[21:22].strip(), int(l[22:26]), l[12:16], l[17:20]
        idx = pdb_idx_s.index((chain,resNo))
        for i_atm, tgtatm in enumerate(ChemData().aa2long[ChemData().aa2num[aa]]):
            if tgtatm == atom:
                xyz[idx,i_atm,:] = [float(l[30:38]), float(l[38:46]), float(l[46:54])]
                break

    # save atom mask
    mask = np.logical_not(np.isnan(xyz[...,0]))
    xyz[np.isnan(xyz[...,0])] = 0.0
    if lddt_mask == True:
        plddt = np.array(plddt)
        mask_lddt = np.full_like(mask, False)
        mask_lddt[plddt > .85, 5:] = True
        mask_lddt[plddt > .70, :5] = True
        mask = np.logical_and(mask, mask_lddt)

    return xyz,mask,np.array(idx_s), np.array(seq)

#'''
def parse_pdb_lines(lines):

    # indices of residues observed in the structure
    res = [(l[21:22].strip(), l[22:26],l[17:20], l[60:66].strip()) for l in lines if l[:4]=="ATOM" and l[12:16].strip() in ["CA", "P"]] # (chain letter, res num, aa)
    pdb_idx_s = [(r[0], int(r[1])) for r in res]
    idx_s = [int(r[1]) for r in res]

    # 4 BB + up to 10 SC atoms
    xyz = np.full((len(idx_s), ChemData().NTOTAL, 3), np.nan, dtype=np.float32)
    for l in lines:
        if l[:4] != "ATOM":
            continue
        chain, resNo, atom, aa = l[21:22].strip(), int(l[22:26]), l[12:16], l[17:20]
        idx = pdb_idx_s.index((chain,resNo))
        for i_atm, tgtatm in enumerate(ChemData().aa2long[ChemData().aa2num[aa]]):
            if tgtatm == atom:
                xyz[idx,i_atm,:] = [float(l[30:38]), float(l[38:46]), float(l[46:54])]
                break

    # save atom mask
    mask = np.logical_not(np.isnan(xyz[...,0]))
    xyz[np.isnan(xyz[...,0])] = 0.0

    return xyz,mask,np.array(idx_s)


def parse_templates(item, params):

    # init FFindexDB of templates
    ### and extract template IDs
    ### present in the DB
    ffdb = FFindexDB(read_index(params['FFDB']+'_pdb.ffindex'),
                     read_data(params['FFDB']+'_pdb.ffdata'))
    #ffids = set([i.name for i in ffdb.index])

    # process tabulated hhsearch output to get
    # matched positions and positional scores
    infile = params['DIR']+'/hhr/'+item[-2:]+'/'+item+'.atab'
    hits = []
    for l in open(infile, "r").readlines():
        if l[0]=='>':
            key = l[1:].split()[0]
            hits.append([key,[],[]])
        elif "score" in l or "dssp" in l:
            continue
        else:
            hi = l.split()[:5]+[0.0,0.0,0.0]
            hits[-1][1].append([int(hi[0]),int(hi[1])])
            hits[-1][2].append([float(hi[2]),float(hi[3]),float(hi[4])])

    # get per-hit statistics from an .hhr file
    # (!!! assume that .hhr and .atab have the same hits !!!)
    # [Probab, E-value, Score, Aligned_cols, 
    # Identities, Similarity, Sum_probs, Template_Neff]
    lines = open(infile[:-4]+'hhr', "r").readlines()
    pos = [i+1 for i,l in enumerate(lines) if l[0]=='>']
    for i,posi in enumerate(pos):
        hits[i].append([float(s) for s in re.sub('[=%]',' ',lines[posi]).split()[1::2]])
        
    # parse templates from FFDB
    for hi in hits:
        #if hi[0] not in ffids:
        #    continue
        entry = get_entry_by_name(hi[0], ffdb.index)
        if entry == None:
            continue
        data = read_entry_lines(entry, ffdb.data)
        hi += list(parse_pdb_lines(data))

    # process hits
    counter = 0
    xyz,qmap,mask,f0d,f1d,ids = [],[],[],[],[],[]
    for data in hits:
        if len(data)<7:
            continue
        
        qi,ti = np.array(data[1]).T
        _,sel1,sel2 = np.intersect1d(ti, data[6], return_indices=True)
        ncol = sel1.shape[0]
        if ncol < 10:
            continue
        
        ids.append(data[0])
        f0d.append(data[3])
        f1d.append(np.array(data[2])[sel1])
        xyz.append(data[4][sel2])
        mask.append(data[5][sel2])
        qmap.append(np.stack([qi[sel1]-1,[counter]*ncol],axis=-1))
        counter += 1
    if len(xyz) == 0:
        return None,None,None,None,None,None
    xyz = np.vstack(xyz).astype(np.float32)
    mask = np.vstack(mask).astype(bool)
    qmap = np.vstack(qmap).astype(np.long)
    f0d = np.vstack(f0d).astype(np.float32)
    f1d = np.vstack(f1d).astype(np.float32)
    ids = ids
        
    return xyz,mask,qmap,f0d,f1d,ids

def parse_templates_raw(ffdb, hhr_fn, atab_fn, max_templ=20):
    # process tabulated hhsearch output to get
    # matched positions and positional scores
    hits = []
    for l in open(atab_fn, "r").readlines():
        if l[0]=='>':
            if len(hits) == max_templ:
                break
            key = l[1:].split()[0]
            hits.append([key,[],[]])
        elif "score" in l or "dssp" in l:
            continue
        else:
            hi = l.split()[:5]+[0.0,0.0,0.0]
            hits[-1][1].append([int(hi[0]),int(hi[1])])
            hits[-1][2].append([float(hi[2]),float(hi[3]),float(hi[4])])

    # get per-hit statistics from an .hhr file
    # (!!! assume that .hhr and .atab have the same hits !!!)
    # [Probab, E-value, Score, Aligned_cols,
    # Identities, Similarity, Sum_probs, Template_Neff]
    lines = open(hhr_fn, "r").readlines()
    pos = [i+1 for i,l in enumerate(lines) if l[0]=='>']
    for i,posi in enumerate(pos[:len(hits)]):
        hits[i].append([float(s) for s in re.sub('[=%]',' ',lines[posi]).split()[1::2]])

    # parse templates from FFDB
    for hi in hits:
        #if hi[0] not in ffids:
        #    continue
        entry = get_entry_by_name(hi[0], ffdb.index)
        if entry == None:
            print ("Failed to find %s in *_pdb.ffindex"%hi[0])
            continue
        data = read_entry_lines(entry, ffdb.data)
        hi += list(parse_pdb_lines_w_seq(data))

    # process hits
    counter = 0
    xyz,qmap,mask,f0d,f1d,ids,seq = [],[],[],[],[],[],[]
    for data in hits:
        if len(data)<7:
            continue
        # print ("Process %s..."%data[0])

        qi,ti = np.array(data[1]).T
        _,sel1,sel2 = np.intersect1d(ti, data[6], return_indices=True)
        ncol = sel1.shape[0]
        if ncol < 10:
            continue
        
        ids.append(data[0])
        f0d.append(data[3])
        f1d.append(np.array(data[2])[sel1])
        xyz.append(data[4][sel2])
        mask.append(data[5][sel2])
        seq.append(data[-1][sel2])
        qmap.append(np.stack([qi[sel1]-1,[counter]*ncol],axis=-1))
        counter += 1

    if len(xyz) == 0:
        return None,None,None,None,None,None,None

    xyz = np.vstack(xyz).astype(np.float32)
    mask = np.vstack(mask).astype(bool)
    qmap = np.vstack(qmap).astype(np.int64)
    f0d = np.vstack(f0d).astype(np.float32)
    f1d = np.vstack(f1d).astype(np.float32)
    seq = np.hstack(seq).astype(np.int64)
    ids = ids

    return torch.from_numpy(xyz), torch.from_numpy(mask), torch.from_numpy(qmap), \
           torch.from_numpy(f0d), torch.from_numpy(f1d), torch.from_numpy(seq), ids

def read_templates(qlen, ffdb, hhr_fn, atab_fn, n_templ=10):
    xyz_t, mask_t, qmap, t1d, seq, ids = parse_templates_raw(ffdb, hhr_fn, atab_fn, max_templ=max(n_templ, 20))
    ntmplatoms = xyz_t.shape[1]

    npick = min(n_templ, len(ids))
    if npick < 1: # no templates
        xyz = torch.full((1,qlen,ChemData().NTOTAL,3),np.nan).float()
        mask = torch.full((1,qlen,ChemData().NTOTAL),False)
        t1d = torch.nn.functional.one_hot(torch.full((1, qlen), 20).long(), num_classes=21).float() # all gaps
        t1d = torch.cat((t1d, torch.zeros((1,qlen,1)).float()), -1)
        return xyz, mask, t1d

    sample = torch.arange(npick)
    #
    xyz = torch.full((npick, qlen, ChemData().NTOTAL, 3), np.nan).float()
    mask = torch.full((npick, qlen, ChemData().NTOTAL), False)
    f1d = torch.full((npick, qlen), 20).long()
    f1d_val = torch.zeros((npick, qlen, 1)).float()
    #
    for i, nt in enumerate(sample):
        sel = torch.where(qmap[:,1] == nt)[0]
        pos = qmap[sel, 0]
        xyz[i, pos] = xyz_t[sel]
        mask[i, pos, :ntmplatoms] = mask_t[sel].bool()
        f1d[i, pos] = seq[sel]
        f1d_val[i,pos] = t1d[sel, 2].unsqueeze(-1)
        xyz[i] = util.center_and_realign_missing(xyz[i], mask[i], seq=f1d[i])

    f1d = torch.nn.functional.one_hot(f1d, num_classes=ChemData().NAATOKENS-1).float()
    f1d = torch.cat((f1d, f1d_val), dim=-1)

    return xyz, mask, f1d


def clean_sdffile(filename):
    # lowercase the 2nd letter of the element name (e.g. FE->Fe) so openbabel can parse it correctly
    lines2 = []
    with open(filename) as f:
        lines = f.readlines()
        num_atoms = int(lines[3][:3])
        for i in range(len(lines)):
            if i>=4 and i<4+num_atoms:
                lines2.append(lines[i][:32]+lines[i][32].lower()+lines[i][33:])
            else:
                lines2.append(lines[i])
    molstring = ''.join(lines2)

    return molstring

def parse_mol(filename, filetype="mol2", string=False, remove_H=True, find_automorphs=True, generate_conformer: bool = False):
    """Parse small molecule ligand.

    Parameters
    ----------
    filename : str
    filetype : str
    string : bool
        If True, `filename` is a string containing the molecule data.
    remove_H : bool
        Whether to remove hydrogen atoms.
    find_automorphs : bool
        Whether to enumerate atom symmetry permutations.

    Returns
    -------
    obmol : OBMol
        openbabel molecule object representing the ligand
    msa : torch.Tensor (N_atoms,) long
        Integer-encoded "sequence" (atom types) of ligand
    ins : torch.Tensor (N_atoms,) long
        Insertion features (all zero) for RF input
    atom_coords : torch.Tensor (N_symmetry, N_atoms, 3) float
        Atom coordinates
    mask : torch.Tensor (N_symmetry, N_atoms) bool
        Boolean mask for whether atom exists
    """
    obConversion = openbabel.OBConversion()
    obConversion.SetInFormat(filetype)
    obmol = openbabel.OBMol()
    if string:
        obConversion.ReadString(obmol,filename)
    elif filetype=='sdf':
        molstring = clean_sdffile(filename)
        obConversion.ReadString(obmol,molstring)
    else:
        obConversion.ReadFile(obmol,filename)
    if generate_conformer:
        builder = openbabel.OBBuilder()
        builder.Build(obmol)
        ff = openbabel.OBForceField.FindForceField("mmff94")
        did_setup = ff.Setup(obmol)
        if did_setup:
            ff.FastRotorSearch()
            ff.GetCoordinates(obmol)
        else:
            raise ValueError(f"Failed to generate 3D coordinates for molecule {filename}.")
    if remove_H:
        obmol.DeleteHydrogens()
        # the above sometimes fails to get all the hydrogens
        i = 1
        while i < obmol.NumAtoms()+1:
            if obmol.GetAtom(i).GetAtomicNum()==1:
                obmol.DeleteAtom(obmol.GetAtom(i))
            else:
                i += 1
    atomtypes = [ChemData().atomnum2atomtype.get(obmol.GetAtom(i).GetAtomicNum(), 'ATM') 
                 for i in range(1, obmol.NumAtoms()+1)]
    msa = torch.tensor([ChemData().aa2num[x] for x in atomtypes])
    # msa と書いているが、原子のインデクスのリストのようだ。炭素は 39。
    ins = torch.zeros_like(msa)

    atom_coords = torch.tensor([[obmol.GetAtom(i).x(),obmol.GetAtom(i).y(), obmol.GetAtom(i).z()] 
                                for i in range(1, obmol.NumAtoms()+1)]).unsqueeze(0) # (1, natoms, 3)
    mask = torch.full(atom_coords.shape[:-1], True) # (1, natoms,)
    
    if find_automorphs:
        atom_coords, mask = util.get_automorphs(obmol, atom_coords[0], mask[0])
    return obmol, msa, ins, atom_coords, mask
